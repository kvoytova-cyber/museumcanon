"""Парсеры значений ячеек.

В конфиге музея поле можно задать как
    birth_year: BeginDate                      # просто колонка
    birth_year: {column: Date, parser: year}   # колонка + парсер

Чтобы добавить свой парсер, достаточно декоратора:

    @parser("my_parser")
    def my_parser(value): ...
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Optional

PARSERS: dict[str, Callable[[Any], Any]] = {}


def parser(name: str):
    """Декоратор: регистрирует функцию в реестре парсеров под именем name."""
    def register(func: Callable[[Any], Any]) -> Callable[[Any], Any]:
        PARSERS[name] = func
        return func
    return register


def get_parser(name: str) -> Callable[[Any], Any]:
    if name not in PARSERS:
        raise KeyError(f"Нет парсера {name!r}. Доступные: {', '.join(sorted(PARSERS))}")
    return PARSERS[name]


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and value.strip() in {"", "nan", "None", "NaN"}


@parser("text")
def text(value: Any) -> Optional[str]:
    """По умолчанию: строка без пробелов по краям, пустое -> None."""
    if is_missing(value):
        return None
    value = str(value).strip()
    return value[:-2] if re.fullmatch(r"-?\d+\.0", value) else value   # 1234.0 -> 1234


@parser("year")
def year(value: Any) -> Optional[int]:
    """Первый правдоподобный год: 1966, '1966.0', 'c. 1915–20', '2001-03-14'. 0 -> None."""
    if is_missing(value):
        return None
    match = re.search(r"(1[0-9]{3}|20[0-9]{2})", str(value))
    return int(match.group(1)) if match else None


@parser("split_list")
def split_list(value: Any) -> list[str]:
    """'123, 456' или '123;456' -> ['123', '456']."""
    if is_missing(value):
        return []
    parts = re.split(r"\s*[,;|]\s*", str(value))
    return [p for p in (text(x) for x in parts) if p]


@parser("born_country")
def born_country(value: Any) -> Optional[str]:
    """Страна рождения из биографии MoMA. Два формата:
    'American, born Russia. 1880–1961'                          -> 'Russia'
    'Born 1866, Russian Empire (now Russia); died 1944, France' -> 'Russia' (берём современную страну)
    """
    if is_missing(value):
        return None
    bio = str(value).strip().replace("U.S.S.R.", "USSR").replace("U.S.A.", "USA").replace("U.K.", "UK")
    if bio.startswith("Born"):
        birth = bio[4:].split(";")[0]
        now = re.search(r"\(now ([^)]+)\)", birth)
        if now:
            return now.group(1).strip()
        parts = [p.strip() for p in birth.split(",") if p.strip() and not re.fullmatch(r"\d{4}", p.strip())]
        return parts[-1] if parts else None
    match = re.search(r"born (?:in )?([^.,;0-9()]+)", bio)
    return (match.group(1).strip() or None) if match else None


@parser("last_part")
def last_part(value: Any) -> Optional[str]:
    """'Moskva, Rossiya' -> 'Rossiya' (страна в конце адреса, как в Tate)."""
    if is_missing(value):
        return None
    return str(value).split(",")[-1].strip() or None


@parser("first_part")
def first_part(value: Any) -> Optional[str]:
    """'Moskva, Rossiya' -> 'Moskva'."""
    if is_missing(value):
        return None
    return str(value).split(",")[0].strip() or None


@parser("clean_nationality")
def clean_nationality(value: Any) -> Optional[str]:
    """'(Russian)' -> 'Russian'; 'Nationality unknown' -> None."""
    value = text(value)
    if not value:
        return None
    value = value.strip("() ")
    return None if value.lower() in {"nationality unknown", "unknown", ""} else value
