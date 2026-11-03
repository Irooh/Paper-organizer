"""Per-user writable storage, independent of an installed executable's location."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path


def data_dir() -> Path:
    """Return/create the local data directory (override for portable/test use)."""
    override = os.environ.get("PAPERDESK_DATA_DIR")
    path = Path(override).expanduser() if override else user_data_path("PaperDesk", appauthor=False)
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "library.sqlite3"


def papers_dir() -> Path:
    path = data_dir() / "papers"
    path.mkdir(parents=True, exist_ok=True)
    return path
