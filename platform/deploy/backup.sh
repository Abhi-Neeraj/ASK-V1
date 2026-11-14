#!/usr/bin/env bash
# Backs up the database and the recordings into a new timestamped folder.
#
#   ./backup.sh [destination folder]        default: ./backups
#
# Run it from this folder on the machine that runs the stack. NOT RUN where it was written (no Docker there);
# run it once by hand and check the files before relying on it, and practise a restore (see
# platform/docs/RUNBOOK.md).
#
# What it makes is SENSITIVE: recordings of people's voices, and voice prints. Keep it somewhere access is
# controlled, and encrypt it before it leaves the machine. The `.env` file (secrets) is NOT included: keep that
# separately, in a password manager or similar.

set -euo pipefail
cd "$(dirname "$0")"
umask 077

root="${1:-./backups}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="$root/$stamp"
mkdir -p "$dest"
dest="$(cd "$dest" && pwd)"

echo "Database..."
docker compose exec -T db pg_dump -U ask -d ask --format=custom > "$dest/database.dump"

echo "Recordings..."
# A throwaway container with the recordings volume mounted. Root, so it can write into the backup folder.
docker compose run --rm --no-deps --user root -v "$dest:/backup" --entrypoint tar api \
  czf /backup/recordings.tar.gz -C /data recordings

(cd "$dest" && sha256sum database.dump recordings.tar.gz > SHA256SUMS)

echo "Done: $dest"
ls -l "$dest"
