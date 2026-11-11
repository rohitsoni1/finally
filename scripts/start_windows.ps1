# Start FinAlly in Docker (Windows PowerShell). Idempotent: safe to run repeatedly.
#   .\scripts\start_windows.ps1            build image if missing, (re)start container
#   .\scripts\start_windows.ps1 -Build     force an image rebuild
#   .\scripts\start_windows.ps1 -NoOpen    don't open the browser
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'

$Image     = if ($env:FINALLY_IMAGE)     { $env:FINALLY_IMAGE }     else { 'finally:latest' }
$Container = if ($env:FINALLY_CONTAINER) { $env:FINALLY_CONTAINER } else { 'finally' }
$Volume    = if ($env:FINALLY_VOLUME)    { $env:FINALLY_VOLUME }    else { 'finally-data' }
$Port      = if ($env:FINALLY_PORT)      { $env:FINALLY_PORT }      else { '8000' }

$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker is not installed or not on PATH.'
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'The Docker daemon is not running. Start Docker Desktop and try again.'
    exit 1
}

if (-not (Test-Path '.env')) {
    if (Test-Path '.env.example') {
        Write-Host 'No .env found; creating one from .env.example (add your OPENROUTER_API_KEY to enable AI chat).'
        Copy-Item '.env.example' '.env'
    } else {
        New-Item -ItemType File '.env' | Out-Null
    }
}

docker image inspect $Image *> $null
if ($Build -or $LASTEXITCODE -ne 0) {
    Write-Host "Building image $Image ..."
    docker build -t $Image .
    if ($LASTEXITCODE -ne 0) { Write-Error 'docker build failed.'; exit 1 }
}

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Replacing existing container $Container ..."
    docker rm -f $Container | Out-Null
}

docker volume create $Volume | Out-Null

# Docker's --env-file is stricter than python-dotenv: no spaces around '=', no
# quotes, no 'export'. Normalise .env into a temp file so either style works.
$EnvFile = [System.IO.Path]::GetTempFileName()
$envLines = foreach ($line in Get-Content '.env') {
    if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        $key = $Matches[1]; $val = $Matches[2]
        if ($val -match '^"(.*)"$' -or $val -match "^'(.*)'$") { $val = $Matches[1] }
        "$key=$val"
    }
}
Set-Content -Path $EnvFile -Value $envLines -Encoding ascii

docker run -d `
    --name $Container `
    -p "${Port}:8000" `
    -v "${Volume}:/app/db" `
    --env-file $EnvFile `
    --restart unless-stopped `
    $Image | Out-Null
$runExit = $LASTEXITCODE
Remove-Item $EnvFile -ErrorAction SilentlyContinue
if ($runExit -ne 0) { Write-Error 'docker run failed.'; exit 1 }

$Url = "http://localhost:$Port"
Write-Host -NoNewline 'Waiting for FinAlly to become healthy'
$healthy = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    Write-Host -NoNewline '.'
    Start-Sleep -Seconds 1
}
Write-Host ''
if (-not $healthy) {
    Write-Warning "Health check did not pass yet. Check logs with: docker logs -f $Container"
}

Write-Host "FinAlly is running at $Url"
Write-Host "Logs: docker logs -f $Container   Stop: .\scripts\stop_windows.ps1"

if (-not $NoOpen) {
    Start-Process $Url
}
