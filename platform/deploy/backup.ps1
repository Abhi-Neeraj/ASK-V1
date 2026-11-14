# Backs up the database and the recordings into a new timestamped folder.
#
#   .\backup.ps1 [-Destination <folder>]        default: .\backups
#
# Run it from this folder on the machine that runs the stack (Docker Desktop is fine). NOT RUN where it was written
# (no Docker there): run it once by hand and check the files before relying on it, and practise a restore (see
# platform/docs/RUNBOOK.md).
#
# What it makes is SENSITIVE: recordings of people's voices, and voice prints. Keep it somewhere access is
# controlled, and encrypt it before it leaves the machine. The `.env` file (secrets) is NOT included: keep that
# separately, in a password manager or similar.

param([string]$Destination = ".\backups")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$dest = Join-Path (New-Item -ItemType Directory -Force $Destination).FullName $stamp
New-Item -ItemType Directory -Force $dest | Out-Null

Write-Host "Database..."
# Not `>`: PowerShell would re-encode the binary dump as text. Let the container write the file itself.
# Single quotes on purpose: $POSTGRES_PASSWORD must reach the container as text, not be expanded here.
docker compose run --rm --no-deps --user root -v "${dest}:/backup" --entrypoint sh db -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h db -U ask -d ask --format=custom -f /backup/database.dump'
if ($LASTEXITCODE -ne 0) { throw "The database backup failed." }

Write-Host "Recordings..."
docker compose run --rm --no-deps --user root -v "${dest}:/backup" --entrypoint tar api czf /backup/recordings.tar.gz -C /data recordings
if ($LASTEXITCODE -ne 0) { throw "The recordings backup failed." }

Get-ChildItem $dest -File | Get-FileHash -Algorithm SHA256 | ForEach-Object { "{0}  {1}" -f $_.Hash.ToLower(), (Split-Path $_.Path -Leaf) } | Set-Content (Join-Path $dest "SHA256SUMS")

Write-Host "Done: $dest"
Get-ChildItem $dest | Format-Table Name, Length -AutoSize
