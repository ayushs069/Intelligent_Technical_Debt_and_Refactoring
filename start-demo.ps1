$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'Opening the dashboard at http://127.0.0.1:8501'
python -m streamlit run dashboard/app.py --server.address=127.0.0.1 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false
exit $LASTEXITCODE
