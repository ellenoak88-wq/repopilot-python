$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv run --frozen python -m repopilot
} else {
    if (!(Test-Path '.venv/Scripts/python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    & .venv/Scripts/python.exe -m pip install --require-hashes -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & .venv/Scripts/python.exe -m repopilot
}
