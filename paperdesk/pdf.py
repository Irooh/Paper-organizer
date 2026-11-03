"""Bounded PDF text extraction and atomic, content-addressed library imports."""

from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from .database import Database

MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
MAX_TEXT_CHARACTERS = 2_000_000


class PdfImportError(ValueError):
    """An actionable document error safe to show in the desktop application."""


@dataclass(frozen=True)
class ExtractedPaper:
    title: str
    authors: str
    year: int | None
    doi: str
    pages: list[str]


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(re.sub(r"[\t ]+", " ", line).strip() for line in text.splitlines()).strip()


def extract_pdf(data: bytes, filename: str = "Paper.pdf") -> ExtractedPaper:
    """Extract text without OCR. Limits prevent accidental unbounded imports."""
    if len(data) > MAX_PDF_BYTES:
        raise PdfImportError("PDF exceeds the 50 MiB import limit.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfImportError("This PDF is password-protected. Import an unlocked copy.")
        if len(reader.pages) > MAX_PAGES:
            raise PdfImportError(f"PDF exceeds the {MAX_PAGES}-page import limit.")
        pages: list[str] = []
        total = 0
        for page in reader.pages:
            text = _clean_text(page.extract_text() or "")
            total += len(text)
            if total > MAX_TEXT_CHARACTERS:
                raise PdfImportError("Extracted text exceeds the two-million-character import limit.")
            pages.append(text)
        if not any(page.strip() for page in pages):
            raise PdfImportError("No readable text was found. Scanned PDFs need OCR before import.")
        metadata = reader.metadata
        title = _clean_text(str(metadata.title or "")) if metadata else ""
        if not title or title.lower() in {"untitled", "unknown"}:
            title = Path(filename).stem.replace("_", " ").strip() or "Untitled paper"
        authors = _clean_text(str(metadata.author or "")) if metadata else ""
        first_page = pages[0]
        doi_match = re.search(r"\b10\.\d{4,9}/[^\s<>\"]+", first_page, flags=re.IGNORECASE)
        doi = doi_match.group(0).rstrip(".,;:)]}") if doi_match else ""
        # This is a suggestion from the document, not an externally verified date.
        year_match = re.search(r"\b((?:19|20)\d{2})\b", first_page)
        year = int(year_match.group(1)) if year_match else None
        return ExtractedPaper(title=title, authors=authors, year=year, doi=doi, pages=pages)
    except PdfImportError:
        raise
    except Exception as exc:
        raise PdfImportError("Unable to read this PDF. It may be damaged or use unsupported encryption.") from exc


def import_pdf(source: Path | str, db: Database, pdf_dir: Path | str) -> int:
    """Copy exactly the bytes extracted; duplicate imports return the existing id."""
    source = Path(source)
    try:
        with source.open("rb") as stream:
            data = stream.read(MAX_PDF_BYTES + 1)
    except OSError as exc:
        raise PdfImportError(f"Cannot open PDF: {exc.strerror or 'file is unavailable'}.") from exc
    if len(data) > MAX_PDF_BYTES:
        raise PdfImportError("PDF exceeds the 50 MiB import limit.")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.get_by_sha256(digest)
    if existing:
        # Restore a missing managed copy when the user reimports its original.
        destination = Path(existing["file_path"])
        if not destination.exists():
            _atomic_write(destination, data)
        return existing["id"]
    extracted = extract_pdf(data, source.name)
    directory = Path(pdf_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = (directory / f"{digest}.pdf").resolve()
    _atomic_write(destination, data)
    try:
        return db.add_paper(
            title=extracted.title, authors=extracted.authors, year=extracted.year,
            doi=extracted.doi, pages=extracted.pages, file_path=str(destination), sha256=digest,
        )
    except sqlite3.IntegrityError:
        # Another import worker may have committed the same fingerprint first.
        existing = db.get_by_sha256(digest)
        if existing:
            return existing["id"]
        raise


def _atomic_write(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".paperdesk-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
