"""JSON files in data/ that the GitHub workflow commits back after each run."""
from __future__ import annotations

import json
from typing import Any

from .config import DATA_DIR


def load(name: str, default: Any) -> Any:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(name: str, data: Any) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    tmp.replace(path)
