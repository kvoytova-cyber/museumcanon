"""Блок 1: чтение данных.

Файл любого поддерживаемого формата (csv, tsv, xlsx, json, jsonl) читается
нужным ридером, а затем по конфигу-маппингу превращается в объекты
Artist / Artwork / Exhibition. Пользователь не меняет код — только указывает,
в каком столбце искать каждое поле.
"""
from __future__ import annotations

import difflib
from abc import ABC, abstractmethod
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml

from .models import Artist, Artwork, Collection, Exhibition
from .parsers import get_parser


class ConfigError(ValueError):
    """Ошибка в конфиге: понятное сообщение, что именно поправить."""


# --------------------------------------------------------------------------
# Ридеры таблиц: наследуются от общего TableReader
# --------------------------------------------------------------------------
class TableReader(ABC):
    suffixes: tuple[str, ...] = ()

    def __init__(self, **options: Any):
        self.options = options

    @classmethod
    def can_read(cls, path: Path) -> bool:
        return path.suffix.lower() in cls.suffixes

    @abstractmethod
    def read(self, path: Path) -> pd.DataFrame:
        ...


class CsvReader(TableReader):
    suffixes = (".csv", ".tsv", ".txt")
    ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")

    def read(self, path: Path) -> pd.DataFrame:
        options = {"low_memory": False, **self.options}
        if path.suffix.lower() == ".tsv":
            options.setdefault("sep", "\t")
        encodings = [options.pop("encoding")] if "encoding" in options else self.ENCODINGS
        for encoding in encodings:
            try:
                return pd.read_csv(path, encoding=encoding, **options)
            except UnicodeDecodeError:
                continue
        raise ConfigError(f"Не удалось подобрать кодировку для {path}; укажите encoding в конфиге")


class ExcelReader(TableReader):
    suffixes = (".xlsx", ".xls", ".xlsm")

    def read(self, path: Path) -> pd.DataFrame:
        return pd.read_excel(path, **self.options)


class JsonReader(TableReader):
    suffixes = (".json", ".jsonl")

    def read(self, path: Path) -> pd.DataFrame:
        lines = path.suffix.lower() == ".jsonl"
        return pd.read_json(path, lines=lines, **self.options)


READERS: list[type[TableReader]] = [CsvReader, ExcelReader, JsonReader]


def read_table(path: str | Path, **options: Any) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Нет файла {path}. Скачайте данные (museumcanon.download) "
                                f"или поправьте path в конфиге")
    for reader_cls in READERS:
        if reader_cls.can_read(path):
            return reader_cls(**options).read(path)
    supported = ", ".join(s for r in READERS for s in r.suffixes)
    raise ConfigError(f"Формат {path.suffix} не поддерживается. Поддерживаются: {supported}")


# --------------------------------------------------------------------------
# Маппинг «каноническое поле -> колонка (+ парсер)»
# --------------------------------------------------------------------------
class FieldMapping:
    """Описание одной таблицы: какие колонки идут в какие поля класса."""

    def __init__(self, table: str, spec: dict[str, Any], target: type, required: tuple[str, ...],
                 extra_fields: tuple[str, ...] = ()):
        allowed = {f.name for f in dc_fields(target)} | set(extra_fields)
        self.table = table
        self.columns: dict[str, tuple[str, str]] = {}
        self.fallbacks: dict[str, str] = {}
        for field_name, value in (spec or {}).items():
            if field_name not in allowed:
                raise ConfigError(f"[{table}] неизвестное поле {field_name!r}. "
                                  f"Допустимые поля: {', '.join(sorted(allowed))}")
            if isinstance(value, str):
                column, parser_name = value, "text"
            elif isinstance(value, dict) and "column" in value:
                column, parser_name = value["column"], value.get("parser", "text")
                if value.get("fallback"):
                    self.fallbacks[field_name] = value["fallback"]
            else:
                raise ConfigError(f"[{table}] поле {field_name!r}: укажите имя колонки "
                                  f"или {{column: ..., parser: ...}}")
            get_parser(parser_name)          # проверяем, что парсер существует
            self.columns[field_name] = (column, parser_name)
        missing = [f for f in required if f not in self.columns]
        if missing:
            raise ConfigError(f"[{table}] не заданы обязательные поля: {', '.join(missing)}")

    def check_columns(self, df: pd.DataFrame) -> None:
        needed = [(f, c) for f, (c, _) in self.columns.items()] + list(self.fallbacks.items())
        for field_name, column in needed:
            if column not in df.columns:
                lowered = {str(c).lower(): str(c) for c in df.columns}
                hint = [lowered[c] for c in difflib.get_close_matches(column.lower(), lowered, n=3)]
                hint_text = f" Похожие: {', '.join(hint)}." if hint else ""
                raise ConfigError(f"[{self.table}] поле {field_name!r}: колонки {column!r} нет "
                                  f"в файле.{hint_text} Все колонки: {', '.join(map(str, df.columns))}")

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Вернуть таблицу с каноническими колонками, значения уже распарсены."""
        self.check_columns(df)
        out = pd.DataFrame(index=df.index)
        for field_name, (column, parser_name) in self.columns.items():
            parse = get_parser(parser_name)
            out[field_name] = df[column].map(parse)
            if field_name in self.fallbacks:             # пусто в основной колонке -> берём запасную
                backup = df[self.fallbacks[field_name]].map(parse)
                out[field_name] = out[field_name].where(out[field_name].notna(), backup)
            if parser_name == "year":                     # иначе pandas превратит годы во float
                out[field_name] = pd.to_numeric(out[field_name], errors="coerce").astype("Int64")
        return out


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return df.astype(object).where(df.notna(), None).to_dict("records")


# --------------------------------------------------------------------------
# Загрузчик музея по конфигу
# --------------------------------------------------------------------------
class MuseumLoader:
    """Читает конфиг музея и собирает Collection.

    Конфиг (YAML или dict):
        name: MoMA
        tables:
          artists:     {path: ..., fields: {artist_id: ..., name: ..., ...}}
          artworks:    {path: ..., fields: {artwork_id: ..., artist_ids: ..., ...}}
          exhibitions: {path: ..., where: {...}, fields: {...}, artist_fields: {...}}
    Любая таблица необязательна, кроме того, что нужен хотя бы один источник художников.
    """

    def __init__(self, config: dict[str, Any] | str | Path, base_dir: Optional[str | Path] = None,
                 skip_missing: bool = False):
        if isinstance(config, (str, Path)):
            config_path = Path(config)
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.config: dict[str, Any] = config
        self.name: str = config.get("name") or "museum"
        self.tables: dict[str, Any] = dict(config.get("tables") or {})
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        if not self.tables:
            raise ConfigError("В конфиге нет секции tables")
        if skip_missing:                       # нет файла -> работаем с тем, что есть
            for table in list(self.tables):
                if not self.path(table).exists():
                    print(f"[{self.name}] нет файла {self.path(table)} — таблица {table} пропущена")
                    del self.tables[table]
        self.mappings = self._build_mappings()

    def _build_mappings(self) -> dict[str, FieldMapping]:
        mappings = {}
        if "artists" in self.tables:
            mappings["artists"] = FieldMapping(
                "artists", self.tables["artists"].get("fields"), Artist, ("artist_id", "name"))
        if "artworks" in self.tables:
            mappings["artworks"] = FieldMapping(
                "artworks", self.tables["artworks"].get("fields"), Artwork, ("artwork_id", "artist_ids"))
        if "exhibitions" in self.tables:
            table = self.tables["exhibitions"]
            mappings["exhibitions"] = FieldMapping(
                "exhibitions", table.get("fields"), Exhibition, ("exhibition_id", "artist_id"),
                extra_fields=("artist_id",))
            if table.get("artist_fields"):
                mappings["exhibition_artists"] = FieldMapping(
                    "exhibitions.artist_fields", table["artist_fields"], Artist, ("name",))
        return mappings

    def path(self, table: str) -> Path:
        path = Path(self.tables[table]["path"])
        return path if path.is_absolute() else self.base_dir / path

    def _read(self, table: str) -> pd.DataFrame:
        spec = self.tables[table]
        df = read_table(self.path(table), **(spec.get("read_options") or {}))
        for column, allowed in (spec.get("where") or {}).items():
            allowed = allowed if isinstance(allowed, list) else [allowed]
            df = df[df[column].isin(allowed)]
        return df

    def _id(self, raw: Any) -> str:
        return f"{self.name}:{raw}"

    def load(self) -> Collection:
        artists: dict[str, Artist] = {}
        artworks: list[Artwork] = []
        exhibitions: list[Exhibition] = []

        if "artists" in self.mappings:
            for rec in _records(self.mappings["artists"].apply(self._read("artists"))):
                if rec["artist_id"] is None:
                    continue
                rec["artist_id"] = self._id(rec["artist_id"])
                artists[rec["artist_id"]] = Artist(**rec, museum=self.name)

        if "artworks" in self.mappings:
            for rec in _records(self.mappings["artworks"].apply(self._read("artworks"))):
                if rec["artwork_id"] is None:
                    continue
                ids = rec.pop("artist_ids") or []
                ids = ids if isinstance(ids, list) else [ids]
                artworks.append(Artwork(**rec, artist_ids=[self._id(i) for i in ids], museum=self.name))

        if "exhibitions" in self.mappings:
            raw = self._read("exhibitions")
            table = self.mappings["exhibitions"].apply(raw)
            if "exhibition_artists" in self.mappings:
                people = self.mappings["exhibition_artists"].apply(raw)
                table = table.join(people.drop(columns=["artist_id"], errors="ignore"))
            table = table[table["artist_id"].notna()]
            by_id: dict[str, Exhibition] = {}
            for rec in _records(table):
                artist_id = self._id(rec["artist_id"])
                ex_id = self._id(rec["exhibition_id"])
                exhibition = by_id.setdefault(ex_id, Exhibition(
                    ex_id, title=rec.get("title"), start_year=rec.get("start_year"), museum=self.name))
                exhibition.artist_ids.add(artist_id)
                if artist_id not in artists and rec.get("name"):
                    person = {k: rec.get(k) for k in self.mappings.get(
                        "exhibition_artists").columns} if "exhibition_artists" in self.mappings else {}
                    artists[artist_id] = Artist(artist_id=artist_id, museum=self.name,
                                                **{k: v for k, v in person.items() if k != "artist_id"})
            exhibitions = list(by_id.values())

        if not artists:
            raise ConfigError(f"[{self.name}] не загружено ни одного художника: "
                              f"нужна таблица artists или exhibitions.artist_fields")
        return Collection(self.name, artists.values(), artworks, exhibitions)


def load_collection(config: dict[str, Any] | str | Path, base_dir: Optional[str | Path] = None,
                    skip_missing: bool = False) -> Collection:
    """Короткий вызов: load_collection('configs/moma.yaml')."""
    return MuseumLoader(config, base_dir, skip_missing).load()
