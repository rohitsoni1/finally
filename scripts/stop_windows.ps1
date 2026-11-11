# Stop and remove the FinAlly container (Windows PowerShell). Data volume is kept.
$ErrorActionPreference = 'Stop'

$Container = if ($env:FINALLY_CONTAINER) { $env:FINALLY_CONTAINER } else { 'finally' }
$Volume    = if ($env:FINALLY_VOLUME)    { $env:FINALLY_VOLUME }    else { 'finally-data' }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'docker is not installed or not on PATH.'
    exit 1
}

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    docker rm -f $Container | Out-Null
    Write-Host "Stopped and removed container $Container."
} else {
    Write-Host "Container $Container is not running."
}
Write-Host "Data volume $Volume was kept (remove it with: docker volume rm $Volume)."
