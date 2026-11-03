# PaperDesk

A Python desktop research library for Windows, with a resizable PySide6 interface, local SQLite storage, PDF text extraction, rich notes, and evidence-grounded AI paper reviews.

## What it does

- Import one or more PDFs into a managed local library; duplicate files reuse the existing entry.
- Search papers and edit title, authors, year, DOI, reading status, and a separate checked flag.
- Read extracted text beside rich notes. Notes save after a short debounce, on focus loss, when switching papers, and when closing.
- Generate and retain multiple Markdown reviews per paper in a background worker. Reviews use a methodological skeptic, a domain specialist, and an editor, with numbered source passages and a supporting evidence appendix.
- Switch between dark and light themes. Keep your API key in memory for the current session.

The review engine **adapts the STORM method** (perspective assignment, question/answer discourse, synthesis) to a single document. It is an independent implementation, not the Stanford `knowledge-storm` package or its web-based research pipeline. It cannot verify novelty against other publications or independently validate experiments. Its system prompts explicitly distinguish claims from verified findings.

## Quick start on Windows

Install Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/), then use PowerShell in the repository directory:

```powershell
uv sync --frozen --python 3.12 --extra dev --extra build
uv run --frozen python -m paperdesk
```

Import a text-based PDF, select it in the library, and type notes in the split reading pane. Check the metadata because PDF metadata and automatic year/DOI hints are often incomplete. To generate a review, enter an OpenAI API key and a model available to your account in the review controls, then run the engine. API usage incurs provider charges.

No API key is required to import, read, organize, or annotate papers. No backend service, browser, Docker, database server, or LangChain installation is required.

## Build a single Windows executable

**Run PyInstaller on Windows**; it does not cross-compile a Windows `.exe` from Linux. These exact commands use the checked-in lockfile:

```powershell
uv sync --frozen --python 3.12 --extra dev --extra build
uv run --frozen --extra dev --extra build python -m pytest -q
uv run --frozen --extra dev --extra build python -m PyInstaller --noconfirm --clean --windowed --onefile --name PaperDesk --paths . paperdesk/__main__.py
.\dist\PaperDesk.exe
```

Alternatively, run `powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1`. The script checks every command's exit status. It requires `uv` and runs from the repository root automatically. The Windows GitHub Actions workflow also runs tests and uploads the executable as an artifact.

The executable bundles Python and Qt. Users can double-click it without installing Python. One-file executables extract bundled components into a temporary directory during startup. User data lives separately, not beside the `.exe`. Sign and test the executable on supported Windows machines before distributing it; no code-signing certificate is bundled. CI configuration is provided, but an actual Windows build must be run to validate Windows packaging.

## Project structure

```text
Paper-organizer/
├── paperdesk/
│   ├── __init__.py       # Package version
│   ├── __main__.py       # GUI startup, logging, single-instance lock
│   ├── paths.py          # Per-user writable storage
│   ├── database.py       # SQLite schema and transactional operations
│   ├── pdf.py            # Validated PDF extraction and managed import
│   ├── prompts.py        # Exact system prompts for the review personas
│   ├── storm.py          # Retrieval, discourse, synthesis, citation checks
│   ├── theme.py          # Light and dark widget palettes
│   └── ui.py             # Library, reader, notes, review worker
├── tests/                # Storage, extraction, engine, and GUI tests
├── scripts/build-windows.ps1
├── .github/workflows/windows.yml
├── docs/ARCHITECTURE.md
├── pyproject.toml
└── uv.lock
```

## Data and privacy

By default, data lives in the per-user `PaperDesk` application data directory (on Windows, typically `%LOCALAPPDATA%\PaperDesk`). Set `PAPERDESK_DATA_DIR` to an absolute directory before launching to use a separate library. PDFs are copied into its managed PDF folder; original files are never modified. Metadata, extracted pages, rich HTML notes, and review history live in SQLite. Back up the entire data directory with PaperDesk closed.

The library is local, but **AI review sends the paper title and selected text excerpts, plus the generated internal discourse, to OpenAI over HTTPS**. The interface discloses this before running reviews. Use documents you are authorized to send to that provider. Keys are not stored in the database, settings, or repository. An `OPENAI_API_KEY` already present in the launch environment may be used by the review controls. The model field defaults to `gpt-4.1-mini`; availability depends on your account. There is no offline model or pretend review fallback.

Review generation is cancellable between requests. A request already in progress may run until its timeout; cancelling does not undo provider charges. Libraries remain usable while work runs, and review results are associated with the paper that started the job even if the selection changes. Large papers are searched in bounded chunks; reviews cover retrieved passages, not every sentence. An evidence appendix exposes the supporting text. Valid citation identifiers do not prove the model's interpretation is correct.

Scanned image-only PDFs need external OCR before import. Password-protected files are not supported unless readable without a password. The reader shows extracted text, not a faithful rendered PDF page. Rich notes support inline styling and lists; they are not a full word processor. SQLite and copied PDFs are not encrypted at rest.

## Development and checks

```powershell
uv sync --frozen --extra dev --extra build
uv run --frozen --extra dev python -m pytest -q
uv run --frozen python -m paperdesk
```

On a Linux machine without a display, run the test suite with `QT_QPA_PLATFORM=offscreen`. Qt still needs its platform's shared libraries. The tests use a fake provider for repeatable review-engine validation, so they do not incur API charges or prove live provider access. A real review requires a valid key, network access to `api.openai.com`, and a model permitted by that key.

Schema details, worker lifecycle, retrieval limits, and persona prompts are described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The literal prompts are maintained in [paperdesk/prompts.py](paperdesk/prompts.py).
