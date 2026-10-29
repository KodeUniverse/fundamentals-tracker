# fundamental-data-feed

Ingestion poller: EDGAR XBRL company facts, 10-K narrative sections, and Yahoo
Finance prices, refreshed on `POLL_INTERVAL_SECONDS`.

- `sources/` — one adapter per public source
- `db.py` — persistence, one transaction per ticker
- `snapshot.py` — seeds an empty database from the committed dump
- `main.py` — cycle loop and configuration

Start it with `docker compose up` from the repository root; see the root
`README.md`. Tests: `uv run pytest`.
