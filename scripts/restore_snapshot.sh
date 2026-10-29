#!/usr/bin/env bash
# Load snapshot/tracker.sql.gz into the running database.
#
# Destructive by design: the dump recreates the schema, so anything in the
# database is replaced. Use it to get back to a known state without waiting for
# an ingest, or to check that the committed snapshot is intact.

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
IN="snapshot/tracker.sql.gz"

if [[ ! -f "$IN" ]]; then
    echo "no snapshot at $IN; create one with scripts/snapshot.sh" >&2
    exit 1
fi

if ! docker compose ps --services --status running 2>/dev/null | grep -qx db; then
    echo "database is not running; start it with: docker compose up -d" >&2
    exit 1
fi

gunzip -c "$IN" | docker compose exec -T db psql \
    -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q >/dev/null

echo "restored $IN"
