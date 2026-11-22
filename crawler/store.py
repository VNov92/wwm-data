"""Atomic JSON load and save helpers for crawler output files."""

import json
import os
from pathlib import Path


def load(path: Path) -> dict | None:
    """Load a JSON object from path, returning None if missing or invalid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_atomic(path: Path, data: dict) -> None:
    """Write data as UTF-8 JSON atomically via a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
