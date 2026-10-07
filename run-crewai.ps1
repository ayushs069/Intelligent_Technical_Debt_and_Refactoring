$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$crewPython = Join-Path $env:USERPROFILE '.venvs\techdebt-crewai\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $crewPython)) {
    throw 'CrewAI environment is missing. See DEPLOYMENT.md.'
}
$env:OTEL_SDK_DISABLED = 'true'
$env:CREWAI_TRACING_ENABLED = 'false'
& $crewPython run_prioritisation.py --backend crewai --mode chain @args
exit $LASTEXITCODE
