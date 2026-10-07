$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'Starting the live analysis dashboard at http://127.0.0.1:8600'
python run_live.py
exit $LASTEXITCODE
