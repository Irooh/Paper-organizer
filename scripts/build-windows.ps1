$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if ($env:OS -ne 'Windows_NT') { throw 'Build the Windows executable on Windows.' }
uv sync --frozen --python 3.12 --extra dev --extra build
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
uv run --frozen --extra dev --extra build python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
uv run --frozen --extra dev --extra build python -m PyInstaller --noconfirm --clean --windowed --onefile --name PaperDesk --paths . paperdesk/__main__.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
Write-Output 'Built dist/PaperDesk.exe. Test PDF import, note persistence, and a live API review on Windows before release.'
