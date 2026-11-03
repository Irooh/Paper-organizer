# Validation record

Validated in this Linux cloud environment with Python 3.12.14 and the provided
`uv.lock` dependency versions:

- `uv --cache-dir /workspace/.cache/uv sync --frozen --python 3.12 --extra dev --extra build` completed, and rerunning it required no changes.
- `uv --cache-dir /workspace/.cache/uv run --frozen --extra dev --extra build python -m pytest -q` executed **46 passing tests**: 18 database/PDF tests, 17 engine tests, and 11 Qt GUI tests. Headless Linux test runs use Qt's offscreen platform.
- Five repeated full-suite subprocess runs passed after addressing GUI/worker lifecycle issues. Regression tests cover both finishing during the close-confirmation dialog and cancelling before closing.
- A real application-entry-point check rendered a saved paper and rich notes, captured the window, and exited cleanly.
- The documented PyInstaller one-file command completed on Linux. The resulting Linux binary launched Qt and initialized the SQLite schema in an isolated temporary data directory. The smoke-test process was then stopped.
- Python compile checks completed successfully.

No live OpenAI requests were made; provider behavior is tested with an injected
deterministic client. The evidence checks validate source identifiers and source
excerpts, not the truth of model interpretations.

The Windows workflow, PowerShell build script, and exact `.exe` build commands are
included, but a Windows executable was **not** built or run on this Linux machine.
Run those checks on Windows and exercise a live review with your own API account
before releasing the application. This validation does not establish that the
cloud environment has been published or restored successfully in a new task.
