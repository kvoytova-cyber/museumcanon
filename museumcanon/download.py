"""Скачивание данных по ссылкам из конфигов (поле url у таблицы)."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import requests
import yaml


def download_file(url: str, path: Path, force: bool = False) -> bool:
    if path.exists() and path.stat().st_size > 1000 and not force:
        print(f"уже есть  {path} ({path.stat().st_size / 1e6:.1f} МБ)")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(path, "wb") as f:
                for chunk in response.iter_content(1 << 20):
                    f.write(chunk)
    except requests.RequestException as error:
        print(f"ОШИБКА    {path}: {error}\n          скачайте вручную: {url}")
        return False
    if path.read_bytes()[:40].startswith(b"version https://git-lfs"):
        path.unlink()
        print(f"ОШИБКА    {path}: пришёл указатель Git LFS вместо данных\n          скачайте вручную: {url}")
        return False
    print(f"скачан    {path} ({path.stat().st_size / 1e6:.1f} МБ)")
    return True


def download(configs: str | Path | Iterable[str | Path], base_dir: str | Path = ".",
             force: bool = False) -> None:
    """Скачать все таблицы, для которых в конфиге указан url."""
    if isinstance(configs, (str, Path)):
        configs = [configs]
    for config_path in configs:
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        for table in (config.get("tables") or {}).values():
            if table.get("url"):
                download_file(table["url"], Path(base_dir) / table["path"], force)
