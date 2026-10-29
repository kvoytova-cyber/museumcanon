"""Модель данных: художник, работа, выставка, коллекция.

Классы ничего не знают о конкретном музее. Любой датасет приводится к ним
через конфиг-маппинг (см. readers.py), поэтому весь остальной код
(предобработка, визуализация) работает с любым музеем одинаково.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

import pandas as pd


def normalize_name(name: str) -> str:
    """'Kandinsky, Wassily' и 'Wassily  Kandinsky' -> 'wassily kandinsky'."""
    name = str(name or "")
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first} {last}"
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"[^\w\s-]", " ", name.lower())
    return re.sub(r"\s+", " ", name).strip()


@dataclass(eq=False)
class Artist:
    artist_id: str
    name: str
    nationality: Optional[str] = None
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    birth_place: Optional[str] = None
    birth_country: Optional[str] = None
    gender: Optional[str] = None
    wikidata_id: Optional[str] = None
    museum: Optional[str] = None
    excluded: bool = False          # ручное исключение из групп (см. Cleaner)

    @property
    def key(self) -> tuple:
        """По чему считаем двух художников одним человеком, даже из разных музеев."""
        return normalize_name(self.name), self.birth_year

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Artist):
            return NotImplemented
        return self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)

    def __str__(self) -> str:
        years = f"{self.birth_year or '?'}–{self.death_year or ''}"
        return f"{self.name} ({years})"


@dataclass(eq=False)
class Artwork:
    artwork_id: str
    artist_ids: list[str] = field(default_factory=list)
    title: Optional[str] = None
    creation_year: Optional[int] = None
    acquisition_year: Optional[int] = None
    medium: Optional[str] = None
    classification: Optional[str] = None
    department: Optional[str] = None
    credit_line: Optional[str] = None
    museum: Optional[str] = None

    GIFT_WORDS = ("gift", "given", "bequest", "presented", "donat", "bequeathed", "дар")
    PURCHASE_WORDS = ("purchase", "fund", "purchased", "acquired", "покупк")

    @property
    def acquisition_type(self) -> str:
        """Дар / покупка / другое — по строке credit line."""
        text = (self.credit_line or "").lower()
        if any(w in text for w in self.GIFT_WORDS) or re.fullmatch(r"the .+ collection", text):
            return "дар"            # «The Louis E. Stern Collection» в MoMA — переданная коллекция
        if any(w in text for w in self.PURCHASE_WORDS):
            return "покупка"
        return "другое" if text else "нет данных"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Artwork):
            return NotImplemented
        return (self.museum, self.artwork_id) == (other.museum, other.artwork_id)

    def __hash__(self) -> int:
        return hash((self.museum, self.artwork_id))

    def __lt__(self, other: "Artwork") -> bool:
        """Сортировка по году поступления; работы без даты — в конец."""
        big = 10_000
        return (self.acquisition_year or big) < (other.acquisition_year or big)


@dataclass(eq=False)
class Exhibition:
    exhibition_id: str
    title: Optional[str] = None
    start_year: Optional[int] = None
    artist_ids: set[str] = field(default_factory=set)
    museum: Optional[str] = None

    def __len__(self) -> int:
        return len(self.artist_ids)

    def __contains__(self, artist: Artist | str) -> bool:
        artist_id = artist.artist_id if isinstance(artist, Artist) else artist
        return artist_id in self.artist_ids

    def __iter__(self) -> Iterator[str]:
        return iter(self.artist_ids)

    def __lt__(self, other: "Exhibition") -> bool:
        return (self.start_year or 10_000) < (other.start_year or 10_000)


class Collection:
    """Один музей (или объединение музеев): художники + работы + выставки.

    Агрегирует объекты Artist, Artwork, Exhibition.
    """

    def __init__(self, name: str, artists: Iterable[Artist] = (),
                 artworks: Iterable[Artwork] = (), exhibitions: Iterable[Exhibition] = ()):
        self.name = name
        self.artists: dict[str, Artist] = {a.artist_id: a for a in artists}
        self.artworks: list[Artwork] = list(artworks)
        self.exhibitions: list[Exhibition] = list(exhibitions)

    # --- dunder-методы ---------------------------------------------------
    def __len__(self) -> int:
        return len(self.artworks)

    def __iter__(self) -> Iterator[Artwork]:
        return iter(self.artworks)

    def __contains__(self, item: Artist | Artwork) -> bool:
        if isinstance(item, Artwork):
            return item in set(self.artworks)
        if isinstance(item, Artist):
            return item in set(self.artists.values())
        return False

    def __add__(self, other: "Collection") -> "Collection":
        """moma + tate -> одна коллекция. Id уже уникальны (с префиксом музея)."""
        if not isinstance(other, Collection):
            return NotImplemented
        return Collection(
            f"{self.name} + {other.name}",
            artists=[*self.artists.values(), *other.artists.values()],
            artworks=[*self.artworks, *other.artworks],
            exhibitions=[*self.exhibitions, *other.exhibitions],
        )

    def __repr__(self) -> str:
        return (f"Collection({self.name!r}: {len(self.artists)} художников, "
                f"{len(self.artworks)} работ, {len(self.exhibitions)} выставок)")

    # --- удобные методы --------------------------------------------------
    @property
    def museums(self) -> list[str]:
        names = {a.museum for a in self.artworks} | {e.museum for e in self.exhibitions}
        return sorted(n for n in names if n)

    def artists_of(self, artwork: Artwork) -> list[Artist]:
        return [self.artists[i] for i in artwork.artist_ids if i in self.artists]

    def artworks_by(self, artist: Artist) -> list[Artwork]:
        return [w for w in self.artworks if artist.artist_id in w.artist_ids]

    def find(self, name: str) -> list[Artist]:
        needle = normalize_name(name)
        return [a for a in self.artists.values() if needle in normalize_name(a.name)]

    def unique_artists(self) -> set[Artist]:
        """Художники без дублей между музеями (через Artist.__eq__/__hash__)."""
        return set(self.artists.values())

    # --- выгрузка в таблицы для анализа ----------------------------------
    def artists_frame(self) -> pd.DataFrame:
        return pd.DataFrame([vars(a) for a in self.artists.values()])

    def artworks_frame(self) -> pd.DataFrame:
        """Одна строка = пара (работа, художник)."""
        rows = []
        for w in self.artworks:
            base = {k: v for k, v in vars(w).items() if k != "artist_ids"}
            base["acquisition_type"] = w.acquisition_type
            for artist_id in w.artist_ids or [None]:
                rows.append({**base, "artist_id": artist_id})
        return pd.DataFrame(rows)

    def exhibitions_frame(self) -> pd.DataFrame:
        """Одна строка = пара (выставка, художник)."""
        rows = [{"exhibition_id": e.exhibition_id, "title": e.title, "start_year": e.start_year,
                 "museum": e.museum, "artist_id": a}
                for e in self.exhibitions for a in e.artist_ids]
        return pd.DataFrame(rows)
