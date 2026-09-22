$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$cert = Join-Path $projectRoot "data\certs\localhost.crt"
$key = Join-Path $projectRoot "data\certs\localhost.key"
if (-not (Test-Path -LiteralPath $cert) -or -not (Test-Path -LiteralPath $key)) {
    throw "Local HTTPS certificate is missing. Run scripts/generate_local_cert.py first."
}

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app `
    --host 127.0.0.1 --port 8000 --ssl-certfile $cert --ssl-keyfile $key
