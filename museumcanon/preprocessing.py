"""Блок 2: предобработка и отбор художников.

Cleaner — чистит коллекцию по секции cleaning конфига музея.
Selector — правило «кто входит в группу» (страна рождения / национальность).
Grouping — упорядоченный набор групп, распределяет художников и отмечает эмигрантов.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml

from .models import Artist, Collection

# Разные написания одной страны -> одно каноническое (по-английски).
# Tate пишет страны в транслитерации, MoMA — по-английски и иногда исторически.
COUNTRY_SYNONYMS: dict[str, str] = {
    # Россия / СССР
    "rossiya": "Russia", "russian federation": "Russia", "russian empire": "Russia",
    "sssr": "USSR", "soviet union": "USSR", "u.s.s.r.": "USSR", "siberia": "Russia", "crimea": "Russia",
    # бывшие республики
    "ukrayina": "Ukraine", "the ukraine": "Ukraine", "byelorussia": "Belarus", "belorussia": "Belarus",
    "latvija": "Latvia", "lietuva": "Lithuania", "eesti": "Estonia", "sakartvelo": "Georgia",
    "hayastan": "Armenia", "azərbaycan": "Azerbaijan", "qazaqstan": "Kazakhstan",
    "ozbekiston": "Uzbekistan", "tojikiston": "Tajikistan", "bessarabia": "Moldova",
    # Восточная Европа
    "polska": "Poland", "ceská republika": "Czech Republic", "česká republika": "Czech Republic",
    "czechia": "Czech Republic", "bohemia": "Czech Republic", "moravia": "Czech Republic",
    "slovenská republika": "Slovakia", "magyarország": "Hungary", "românia": "Romania",
    "rumania": "Romania", "transylvania": "Romania", "balgarija": "Bulgaria",
    "jugoslavija": "Yugoslavia", "hrvatska": "Croatia", "slovenija": "Slovenia", "srbija": "Serbia",
    "bosna i hercegovina": "Bosnia and Herzegovina", "makedonija": "North Macedonia",
    "macedonia": "North Macedonia", "shqipëria": "Albania",
    # прочее частое
    "nihon": "Japan", "deutschland": "Germany", "france": "France", "españa": "Spain",
    "italia": "Italy", "nederland": "Netherlands", "the netherlands": "Netherlands", "united states": "USA",
    "united states of america": "USA", "united kingdom": "UK", "england": "UK", "scotland": "UK",
    "zhonghua": "China", "éire": "Ireland", "österreich": "Austria", "schweiz": "Switzerland",
}


def normalize_country(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = re.sub(r"\(.*?\)", "", str(value)).strip(" .")
    return COUNTRY_SYNONYMS.get(value.lower(), value) or None


def nationality_tokens(value: Optional[str]) -> set[str]:
    """'Russian-American' -> {'Russian', 'American'}."""
    return {t for t in re.split(r"[-/,\s]+", value or "") if t}


# --------------------------------------------------------------------------
# Очистка
# --------------------------------------------------------------------------
def step(func: Callable) -> Callable:
    """Декоратор шага очистки: печатает, сколько было и стало после шага."""
    @functools.wraps(func)
    def wrapper(self: "Cleaner", collection: Collection, *args: Any, **kwargs: Any) -> Collection:
        before = (len(collection.artists), len(collection))
        result = func(self, collection, *args, **kwargs)
        after = (len(result.artists), len(result))
        if self.verbose and before != after:
            print(f"  {func.__name__:22} художников {before[0]:>6} → {after[0]:<6} "
                  f"работ {before[1]:>7} → {after[1]}")
        return result
    return wrapper


class Cleaner:
    """Применяет секцию cleaning из конфига музея.

    cleaning:
      drop_names_matching: "Unidentified|Anonymous"   # regex по имени: удалить художника и его работы
      drop_artists: [...]                              # точные имена: удалить
      exclude_from_groups: [...]                       # оставить, но не включать в группы
      overrides: {исходный_id: {поле: значение}}       # ручные поправки
    """

    def __init__(self, cleaning: Optional[dict[str, Any]] = None, verbose: bool = True):
        self.cleaning = cleaning or {}
        self.verbose = verbose

    @classmethod
    def from_config(cls, config_path: str | Path, verbose: bool = True) -> "Cleaner":
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        return cls(config.get("cleaning"), verbose)

    def run(self, collection: Collection) -> Collection:
        if self.verbose:
            print(f"Очистка {collection.name}:")
        collection = self.normalize(collection)
        collection = self.apply_overrides(collection)
        collection = self.drop_by_pattern(collection)
        collection = self.drop_named(collection)
        collection = self.mark_excluded(collection)
        return collection

    @staticmethod
    def _without(collection: Collection, drop_ids: set[str]) -> Collection:
        if not drop_ids:
            return collection
        artists = [a for a in collection.artists.values() if a.artist_id not in drop_ids]
        artworks = [w for w in collection.artworks if not set(w.artist_ids) & drop_ids]
        exhibitions = [replace(e, artist_ids=e.artist_ids - drop_ids) for e in collection.exhibitions]
        return Collection(collection.name, artists, artworks, [e for e in exhibitions if len(e)])

    @step
    def normalize(self, collection: Collection) -> Collection:
        for artist in collection.artists.values():
            artist.birth_country = normalize_country(artist.birth_country)
        return collection

    @step
    def apply_overrides(self, collection: Collection) -> Collection:
        for raw_id, changes in (self.cleaning.get("overrides") or {}).items():
            artist = collection.artists.get(f"{collection.name}:{raw_id}")
            if artist is None:
                continue
            for key, value in (changes or {}).items():
                setattr(artist, key, value)
        return collection

    @step
    def drop_by_pattern(self, collection: Collection) -> Collection:
        pattern = self.cleaning.get("drop_names_matching")
        if not pattern:
            return collection
        regex = re.compile(pattern, re.IGNORECASE)
        drop = {a.artist_id for a in collection.artists.values() if regex.search(a.name or "")}
        return self._without(collection, drop)

    @step
    def drop_named(self, collection: Collection) -> Collection:
        names = set(self.cleaning.get("drop_artists") or [])
        drop = {a.artist_id for a in collection.artists.values() if a.name in names}
        return self._without(collection, drop)

    @step
    def mark_excluded(self, collection: Collection) -> Collection:
        names = set(self.cleaning.get("exclude_from_groups") or [])
        for artist in collection.artists.values():
            if artist.name in names:
                artist.excluded = True
        return collection


# --------------------------------------------------------------------------
# Отбор
# --------------------------------------------------------------------------
@dataclass
class Selector:
    """Правило группы: родился в одной из countries ИЛИ записан с одной из nationalities."""
    name: str
    countries: set[str] = field(default_factory=set)
    nationalities: set[str] = field(default_factory=set)
    born_from: Optional[int] = None
    born_to: Optional[int] = None

    def __post_init__(self) -> None:
        self.countries = {normalize_country(c) for c in self.countries}
        self.nationalities = set(self.nationalities)

    def born_here(self, artist: Artist) -> bool:
        return normalize_country(artist.birth_country) in self.countries

    def has_nationality(self, artist: Artist) -> bool:
        return bool(nationality_tokens(artist.nationality) & self.nationalities)

    def matches(self, artist: Artist) -> bool:
        if artist.excluded:
            return False
        if self.born_from and (artist.birth_year or 0) < self.born_from:
            return False
        if self.born_to and (artist.birth_year or 10_000) > self.born_to:
            return False
        return self.born_here(artist) or self.has_nationality(artist)

    def is_emigre(self, artist: Artist) -> bool:
        """Родился здесь, но музей записал другую национальность (Кандинский: 'French, born Russia')."""
        return self.matches(artist) and self.born_here(artist) \
            and bool(artist.nationality) and not self.has_nationality(artist)

    def __contains__(self, artist: Artist) -> bool:      # `artist in russia`
        return self.matches(artist)

    def __or__(self, other: "Selector") -> "Selector":   # russia | ex_ussr
        return Selector(f"{self.name} + {other.name}", self.countries | other.countries,
                        self.nationalities | other.nationalities)


class Grouping:
    """Упорядоченные группы: художник попадает в первую подходящую."""

    def __init__(self, selectors: Iterable[Selector]):
        self.selectors = list(selectors)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Grouping":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(Selector(g["name"], set(g.get("countries") or []), set(g.get("nationalities") or []),
                            g.get("born_from"), g.get("born_to")) for g in data["groups"])

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.selectors]

    def group_of(self, artist: Artist) -> Optional[Selector]:
        return next((s for s in self.selectors if s.matches(artist)), None)

    def assign(self, collection: Collection) -> dict[str, tuple[str, bool]]:
        """artist_id -> (группа, эмигрант?) только для попавших в группы."""
        result = {}
        for artist in collection.artists.values():
            selector = self.group_of(artist)
            if selector:
                result[artist.artist_id] = (selector.name, selector.is_emigre(artist))
        return result

    def __len__(self) -> int:
        return len(self.selectors)

    def __iter__(self):
        return iter(self.selectors)

    def __repr__(self) -> str:
        return f"Grouping({', '.join(self.names)})"
