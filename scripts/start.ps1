param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Set-Location $TaskRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required' }
}
& .venv/Scripts/python.exe scripts/bootstrap.py
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; rerun this launcher to retry' }
& .venv/Scripts/python.exe -m ard --port $Port
