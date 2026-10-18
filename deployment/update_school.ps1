param(
    [switch]$PrepareOnly,
    [string]$ConnectionFile = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$updateId = 'update-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
$archivePath = Join-Path $projectRoot "dist\$updateId.tar.gz"

if (-not $PrepareOnly) {
    if (-not $ConnectionFile) { $ConnectionFile = Join-Path $PSScriptRoot 'connection.local.json' }
    if (-not (Test-Path -LiteralPath $ConnectionFile -PathType Leaf)) {
        throw 'Copy deployment/connection.example.json to connection.local.json and set your SSH destination before updating.'
    }
    $connection = Get-Content -LiteralPath $ConnectionFile -Raw -Encoding utf8 | ConvertFrom-Json
    $destination = [string]$connection.destination
    $remoteRoot = [string]$connection.remote_root
    if ($destination -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*$' -or $destination -match '\.invalid$') {
        throw 'Set destination to a valid user@host in the private connection settings.'
    }
    if ($remoteRoot -notmatch '^/[A-Za-z0-9_./-]+/remind-bot-centos7$' -or
        @($remoteRoot -split '/' | Where-Object { $_ -in @('.', '..') }).Count -ne 0) {
        throw 'Set remote_root to the absolute bot directory under your server home, without spaces or parent traversal.'
    }
}

$buildArguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'build_update.py'), '--output', $archivePath)
if (-not $PrepareOnly) { $buildArguments += '--check-tests' }
& $pythonPath @buildArguments
if ($LASTEXITCODE -ne 0) { throw 'Local checks or update preparation failed. Nothing was uploaded.' }
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Update: $archivePath"
Write-Host "SHA256: $archiveHash"
if ($PrepareOnly) { return }

Write-Host 'Upload: enter your usual school SSH password when prompted.'
& scp $archivePath "${destination}:${remoteRoot}/${updateId}.tar.gz"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed. The running bot was not changed.' }

# All remote paths stay under the existing bot directory.
$remoteCommand = "umask 077 && cd '$remoteRoot' && printf '%s\n' '$archiveHash  $updateId.tar.gz' | sha256sum -c - && mkdir -p 'updates/$updateId' && tar -xzf '$updateId.tar.gz' -C 'updates/$updateId' && ./python/bin/python3 -s 'updates/$updateId/deployment/apply_update.py' --root '$remoteRoot'"
Write-Host 'Apply: enter your usual school SSH password again when prompted.'
& ssh $destination $remoteCommand
if ($LASTEXITCODE -ne 0) { throw 'The update did not complete. Please share the output (without passwords).' }
Write-Host 'Completed.'
