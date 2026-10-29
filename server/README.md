# fundamentals-tracker-server

Read-only FastAPI service over the Postgres database the feed builds.

- `analysis/` — metric, valuation, and paragraph-diff logic
- `llm/` — tool schemas, tool implementations, and the agent loop
- `db.py` — connection pool and all SQL
- `main.py` — routes and response models

Start it with `docker compose up` from the repository root; see the root
`README.md` for configuration and examples. Tests: `uv run pytest`.
