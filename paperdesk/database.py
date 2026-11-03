"""Thread-safe-by-connection SQLite repository with versioned schema creation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

STATUSES = ("Unread", "In Progress", "Reviewed")
SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    authors TEXT NOT NULL DEFAULT '',
    year INTEGER CHECK(year IS NULL OR year BETWEEN 1000 AND 9999),
    doi TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Unread'
        CHECK(status IN ('Unread', 'In Progress', 'Reviewed')),
    checked INTEGER NOT NULL DEFAULT 0 CHECK(checked IN (0, 1)),
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    pages_json TEXT NOT NULL,
    notes_html TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    markdown TEXT NOT NULL CHECK(length(trim(markdown)) > 0),
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS reviews_paper_id ON reviews(paper_id, id DESC);
"""


class Database:
    """Each call opens its own connection; workers never share SQLite handles."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("This database was created by a newer PaperDesk version.")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _paper(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        paper = dict(row)
        paper["pages"] = json.loads(paper.pop("pages_json"))
        paper["checked"] = bool(paper["checked"])
        return paper

    def list_papers(self, search: str = "") -> list[dict]:
        """Return lightweight library rows; get_paper loads notes and extracted pages."""
        # Treat SQL wildcard characters literally, so searches are unsurprising.
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id,title,authors,year,doi,status,checked,file_path,sha256,created_at,updated_at "
                "FROM papers WHERE title LIKE ? ESCAPE '\\' "
                "OR authors LIKE ? ESCAPE '\\' OR doi LIKE ? ESCAPE '\\' "
                "ORDER BY created_at DESC, id DESC", (pattern, pattern, pattern)
            ).fetchall()
        return [dict(row, checked=bool(row["checked"])) for row in rows]

    def get_paper(self, paper_id: int) -> dict | None:
        with self._connection() as connection:
            return self._paper(connection.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone())

    def get_by_sha256(self, sha256: str) -> dict | None:
        with self._connection() as connection:
            return self._paper(connection.execute("SELECT * FROM papers WHERE sha256=?", (sha256,)).fetchone())

    @staticmethod
    def _validate_metadata(title: str, year: int | None, status: str) -> None:
        if not title.strip():
            raise ValueError("A paper title is required.")
        if year is not None and (isinstance(year, bool) or not isinstance(year, int) or not 1000 <= year <= 9999):
            raise ValueError("Year must be a four-digit number or empty.")
        if status not in STATUSES:
            raise ValueError("Unknown paper status.")

    def add_paper(self, *, title: str, file_path: str, sha256: str, pages: list[str],
                  authors: str = "", year: int | None = None, doi: str = "",
                  status: str = "Unread", checked: bool = False) -> int:
        self._validate_metadata(title, year, status)
        if not pages or not all(isinstance(page, str) for page in pages):
            raise ValueError("Paper pages must be a nonempty list of strings.")
        if not file_path or not sha256:
            raise ValueError("A managed file path and fingerprint are required.")
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO papers(title,authors,year,doi,status,checked,file_path,sha256,pages_json) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (title.strip(), authors.strip(), year, doi.strip(), status, int(bool(checked)),
                 str(file_path), sha256, json.dumps(pages, ensure_ascii=False)),
            )
            return cursor.lastrowid

    def update_metadata(self, paper_id: int, title: str, authors: str, year: int | None,
                        doi: str, status: str, checked: bool) -> None:
        self._validate_metadata(title, year, status)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE papers SET title=?,authors=?,year=?,doi=?,status=?,checked=?,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (title.strip(), authors.strip(), year, doi.strip(), status, int(bool(checked)), paper_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Paper {paper_id} does not exist.")

    def set_checked(self, paper_id: int, checked: bool) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE papers SET checked=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (int(bool(checked)), paper_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Paper {paper_id} does not exist.")

    def save_notes(self, paper_id: int, html: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE papers SET notes_html=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (html, paper_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Paper {paper_id} does not exist.")

    def list_reviews(self, paper_id: int) -> list[dict]:
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM reviews WHERE paper_id=? ORDER BY id DESC", (paper_id,)
            ).fetchall()]

    def add_review(self, paper_id: int, markdown: str, model: str) -> int:
        if not markdown.strip():
            raise ValueError("Cannot save an empty review.")
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO reviews(paper_id,markdown,model) VALUES(?,?,?)", (paper_id, markdown, model)
            )
            connection.execute(
                "UPDATE papers SET status='Reviewed',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (paper_id,),
            )
            return cursor.lastrowid

    def remove_paper(self, paper_id: int) -> None:
        """Remove library data and review history; retain the managed PDF on disk."""
        with self._connection() as connection:
            connection.execute("DELETE FROM papers WHERE id=?", (paper_id,))
