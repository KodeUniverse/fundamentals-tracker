#!/usr/bin/env bash
# Inspect the tracker database. No psql needed on the host.
#
#   scripts/inspect_db.sh                interactive psql session
#   scripts/inspect_db.sh "SELECT ..."   run one query and exit
#
# Inside the session: \dt lists tables, \d+ prices shows indexes and
# constraints, \x auto makes wide rows readable, \timing shows query times.

set -euo pipefail

self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$self")/.."

# Read POSTGRES_* from .env so the script survives a credential change.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

DB_USER="${POSTGRES_USER:-tracker}"
DB_NAME="${POSTGRES_DB:-tracker}"

usage() {
    cat <<'EOF'
Usage: scripts/inspect_db.sh [SQL]

  (no arguments)   open an interactive psql session
  "SQL"            run one query and exit

Examples:
  scripts/inspect_db.sh
  scripts/inspect_db.sh "SELECT ticker, entity_name FROM entities;"
  scripts/inspect_db.sh "SELECT count(*) FROM prices;"
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if ! docker compose ps --services --status running 2>/dev/null | grep -qx db; then
    echo "database is not running; start it with: docker compose up -d" >&2
    exit 1
fi

if [[ $# -eq 0 ]]; then
    exec docker compose exec db psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1
fi

exec docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "$1"
