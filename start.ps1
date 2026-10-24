$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.env')) { throw 'Copy .env.example to .env and configure API keys.' }
function Invoke-Demo {
    & docker compose --env-file .env -p dartch-local-demo -f docker-compose.demo.yml @args
    if ($LASTEXITCODE -ne 0) { throw 'Docker command failed. Check the error above.' }
}
Write-Host 'Starting a new demo. Previous demo data will be reset.'
# Windows PowerShell 5.1 can turn redirected native stderr into a terminating
# error before LASTEXITCODE is checked. Listing an absent image succeeds empty.
$lockImage = @(& docker image ls --quiet python:3.11-slim)
if ($LASTEXITCODE -ne 0) { throw 'Cannot access Docker. Start Docker Desktop first.' }
if ($lockImage.Count -eq 0) {
    & docker pull python:3.11-slim
    if ($LASTEXITCODE -ne 0) { throw 'Cannot download the startup lock image.' }
}
& docker create --name dartch-local-demo-start-lock python:3.11-slim true | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Another startup is running or a stale lock exists. See docs/run.md.' }
try {
    Invoke-Demo build
    Invoke-Demo stop web api business rag
    Invoke-Demo up -d --wait postgres
    Invoke-Demo run --rm --no-deps prepare
    Invoke-Demo up -d --wait
} finally {
    & docker rm dartch-local-demo-start-lock | Out-Null
}
Write-Host 'Ready. Default UI: http://127.0.0.1:8080 (or WEB_PORT in .env)'
Write-Host 'Stop: docker compose --env-file .env -p dartch-local-demo -f docker-compose.demo.yml stop'
