#!/usr/bin/env bash
# Dump the running database to snapshot/tracker.sql.gz.
#
# The result is committed, so the stack can be started with no network at all:
# the feed loads it into an empty database before its first cycle.

set -euo pipefail

self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$self")/.."

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

DB_USER="${POSTGRES_USER:-tracker}"
DB_NAME="${POSTGRES_DB:-tracker}"
OUT="snapshot/tracker.sql.gz"

if ! docker compose ps --services --status running 2>/dev/null | grep -qx db; then
    echo "database is not running; start it with: docker compose up -d" >&2
    exit 1
fi

mkdir -p snapshot

# --clean --if-exists makes the dump restorable over an existing schema, which is
# what the feed does after init.sql has already created the tables.
docker compose exec -T db pg_dump \
    -U "$DB_USER" -d "$DB_NAME" \
    --no-owner --no-privileges --clean --if-exists \
    | gzip -9 >"$OUT"

echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
