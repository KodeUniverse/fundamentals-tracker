# AGENTS.md

Operating guide for AI agents working in this repo. Read this before touching code.

This file is the shared context for every session. Treat it as authoritative, and
keep it current: when a feature lands, a table or endpoint or env var changes, or a
plan gets abandoned, update the matching section plus **Current status** before you
finish. A stale status block makes the next session rebuild things that already
exist, so keeping it accurate is part of the work, not cleanup afterwards.

## What this project is

"Fundamentals Tracker" — Schonfeld Strategic Advisors SWE case study (FE COO team).
A backend service that tracks company fundamentals from three public sources and
answers natural-language questions spanning both the reported numbers and the
narrative in SEC filings.

Source of truth for requirements: `Software Engineer FE Case Study NY.txt`.
Fixed graded universe: NVDA, MSFT, AAPL, GOOGL, ETN — but it must come from
configuration (`TICKERS`), never hardcoded in code.

## Hard requirements (non-negotiable)

1. Three live sources, all public/free:
   - EDGAR XBRL company facts (`data.sec.gov`)
   - EDGAR 10-K narrative: Item 1A (Risk Factors) + Item 7 (MD&A), latest TWO
     annual filings per company
   - Market data via yfinance
2. Postgres schema covering all three. A committed snapshot/seed must let the
   whole stack run without live API access at review time.
3. FastAPI endpoints for fundamentals, prices, derived metrics.
4. Metrics: revenue, net income, EPS, gross margin, operating margin; YoY growth
   and margins; at least one cross-source multiple (trailing P/E or P/S).
   Trailing only — no forward estimates.
5. LLM layer that routes between structured lookups and filing text (tool use,
   retrieval, structured output, or agent loop — not free-text chat). Must decline
   out-of-scope questions (e.g. "forward guidance") rather than invent.
6. `docker compose up` must bring up the full stack. LLM base URL, API key, and
   model name come from environment variables only — never hardcoded.
7. Deliverables also include a README (one-command run, env vars, provider +
   exact model, 2–3 example interactions) and a 1–2 page design writeup.

## Architecture

| Service | Container | Notes |
| --- | --- | --- |
| `db` | `fundamentals-tracker-db` | postgres:17-alpine, port 5432, volume `pgdata`, healthcheck |
| `fundamental-data-feed` | `fundamentals-tracker-feed` | Long-running ingest loop, polls `POLL_INTERVAL_SECONDS` |
| `server` | `fundamentals-tracker-server` | FastAPI, port 8000 (currently a stub) |

Shared `app-net` bridge network. Compose project name: `fundamentals-tracker`.

```
database/init.sql                  schema, applied only on an EMPTY pgdata volume
fundamental-data-feed/
  main.py                          env config, cycle loop, per-ticker transaction
  facts.py                         EDGAR fetch + normalize -> Entity/ConceptRow/FactRow
  db.py                            psycopg3 persistence (upsert + bulk insert)
server/main.py                     FastAPI app
```

Module boundary rule: `facts.py` never imports `db`, `db.py` never imports `edgar`,
and no SQL ever lives in `main.py` or in the server's request handlers.

## Data model (database/init.sql)

- `entities(cik BIGINT PK, entity_name, ticker)` — ticker is display-only, NOT
  unique (GOOGL/GOOG share a CIK).
- `concepts(id, taxonomy, name, label, description)` — UNIQUE (taxonomy, name).
  Local name only, e.g. `RevenueFromContractWithCustomerExcludingAssessedTax`.
- `facts(cik, concept_id, unit, value NUMERIC, start_date, end_date, form_type,
  filed_date, accession, filing_fy, filing_fp, frame)` with natural key
  `UNIQUE NULLS NOT DISTINCT (cik, concept_id, unit, start_date, end_date, accession)`.

Semantics that matter:

- `start_date IS NULL` for instant (balance-sheet) facts — ~41% of rows.
- `filing_fy`/`filing_fp` describe the FILING, not the fact's period. An FY2023
  fact reported in the FY2025 10-K carries `filing_fy = 2025`.
- Restatements are preserved as separate rows by `accession`. Do not collapse them.
- `NULLS NOT DISTINCT` is load-bearing for the natural key. Do not remove it.

## Conventions (already decided — don't relitigate)

- **Sync ingestion.** edgartools is blocking. Use `ThreadPoolExecutor` only if
  parallel fetching is needed; do NOT add async DB writes.
- **psycopg 3**, named parameters, `execute`/`executemany`. No ORM in the feed.
- **Rows are `TypedDict`s** at the DB boundary because psycopg named parameters
  need Mappings. Dataclasses would force an `asdict()` per row.
- **Pass `edgar.Company` explicitly**; one instance per ticker per cycle. No
  `lru_cache` or module-level registry — edgartools already caches parsed facts
  internally (maxsize=1), and hidden global state hurts testability.
- **One transaction per ticker** (`with conn.transaction():`), so entity +
  concepts + facts are atomic.
- **Idempotency**: `ON CONFLICT ... DO NOTHING` on the natural key. Re-running any
  cycle must produce zero new rows.
- All configuration via environment variables. No literal tickers, credentials,
  URLs, or model names in code.
- Keep it simple: prefer removing a branch over adding a fallback for a case that
  cannot occur. Validation only where data is genuinely external and messy.

## Coding style

The bar: a reviewer should be able to read a file top to bottom and understand it
without running it. Simple beats clever, every time.

**Do**

- Write small functions with one job, named after the domain (`collect_concepts`,
  `upsert_entity`), not after the mechanism (`process_data_2`).
- Use guard clauses and early returns over nested `if`s.
- Return plain data (dicts, typed dicts, lists) between layers; keep framework
  objects (`Company`, `FinancialFact`) inside the module that owns them.
- Prefer the standard library and the dependency already in `pyproject.toml` over
  adding a package. When a package removes 100 lines, use it (pandas for
  tabular work, psycopg for SQL) — just don't stack three that overlap.
- Comment the *why* for anything non-obvious (a constraint, an EDGAR quirk, an
  ordering requirement). Never comment the *what* — the code says that.
- Handle errors where you can act on them: per-item inside a loop so one bad
  ticker does not kill the feed, loudly at startup for missing config.
- Delete dead code, unused parameters, and speculative options. Unused flags are
  worse than absent features.

**Don't**

- Don't add abstraction layers for a single caller (no interface class with one
  implementation, no config object with one field).
- Don't add caches, retries, queues, or background threads without a measured
  reason. Compose healthchecks already cover database startup; a retry loop on
  top of that is duplicate machinery.
- Don't write fallbacks for impossible states. If `fact.unit` is always present,
  `fact.unit or "pure"` is noise, not resilience. Let it fail loudly.
- Don't micro-optimize Python. Optimize at the I/O boundary instead — see below.
- Don't leave `print()` for diagnostics; use the module logger.
- Don't put business rules in SQL strings scattered across modules; one query
  per concern, next to the function that runs it.

**Comments and docstrings**

- Write them the way a person would explain it out loud to a teammate. Short
  sentences, plain words.
- No marketing language or filler. Skip "leverage", "utilize", "robust",
  "seamless", "harness", "orchestrate", "comprehensive". Use "use", "works",
  "covers".
- No slang, jokes, emoji, or exclamation marks. Also skip "simply", "just", and
  "obviously", which mostly make the reader feel slow.
- A docstring says what the function returns and any constraint that matters
  (units, nullability, ordering). One or two sentences is usually enough.
- Don't restate the signature. `get_all_facts(company)` does not need a docstring
  saying "Gets all facts for a company".
- Comments explain decisions and edge cases. If a line is obvious, delete the
  comment instead of rewording it.

**Performance, where it actually pays**

- Batch database work: one `executemany` per ticker (~25k rows) rather than
  row-at-a-time inserts. Reach for `COPY` only if ingest grows past ~1M rows.
- One round trip per concern: upsert concepts, then insert facts, inside one
  transaction. Avoid per-row `SELECT` lookups (N+1) — build the id map once.
- Fetch companies in parallel when the network is the bottleneck
  (`ThreadPoolExecutor` over tickers); keep parsing and DB writes single-threaded.
- Index only the queries you actually write. The three `facts` indexes exist for
  entity+concept time series, cross-company period comparison, and per-period
  snapshots; add a fourth only when a real endpoint needs it.
- Use a connection pool (`psycopg_pool`) for the FastAPI server, not for the feed.
  One long-lived connection is correct for a single-threaded poller.
- Cache per cycle, not per process, unless staleness is provably fine.

## Commands

```bash
docker compose up -d --build                 # full stack
docker compose logs -f fundamental-data-feed # follow ingest
docker compose down -v                       # WARNING: destroys the volume
docker compose run --rm -e RUN_ONCE=1 fundamental-data-feed   # single pass

# inspect
docker exec fundamentals-tracker-db psql -U tracker -d tracker -c "
  SELECT e.ticker, count(*) FROM facts f JOIN entities e USING (cik) GROUP BY 1;"

# local run (deps live in fundamental-data-feed/.venv)
fundamental-data-feed/.venv/bin/python fundamental-data-feed/main.py
```

`RUN_ONCE=1` exits after one cycle — use it for tests, snapshot builds, and CI.

## Environment variables

| Var | Purpose |
| --- | --- |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` / `_PORT` | database |
| `DATABASE_URL` | feed uses plain `postgresql://` (psycopg); server uses `postgresql+psycopg://` (SQLAlchemy) |
| `EDGAR_IDENTITY` | "Name email@example.com" — SEC returns 403 without it |
| `TICKERS` | comma-separated universe |
| `POLL_INTERVAL_SECONDS` | feed cycle interval (default 3600) |
| `RUN_ONCE` | single pass then exit |
| `LOG_LEVEL` | default INFO |
| `SNAPSHOT_DIR` / `USE_SNAPSHOT_IF_NO_NETWORK` | offline replay (declared, NOT yet implemented) |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | LLM config (declared, NOT yet used) |

Read env at the entrypoint and pass values down; don't sprinkle `os.getenv`
through library functions. (`db.connect()` reading `DATABASE_URL` is the
deliberate exception — it is the one place a connection is opened.)

## Current status (last verified 2026-10-28)

Update this block as part of any change that moves the project forward: a new
source, table, endpoint, or env var, a decision that is now settled, or work that
was dropped. Bump the date. If another section of this file stops matching the
code, fix the file instead of working around it.

Done:
- XBRL ingestion, live, for all five tickers: 125,545 facts, 1,350 concepts.
- Idempotent re-ingestion, restatement preservation, graceful SIGTERM.
- Containerized feed running as a long-lived poller.

Not started: 10-K narrative ingest, prices/yfinance, snapshot read/write, FastAPI
endpoints, derived + cross-source metrics, LLM routing layer, tests, README,
design writeup.

## Gotchas

- `edgar.set_identity()` is mandatory or SEC 403s.
- `FinancialFact` has no `.cik` and no `.frame` — CIK is passed in from the
  `Company`; `facts.frame` stays NULL until raw XBRL is ingested.
- `get_all_facts()` returns consolidated facts only (0 dimensioned rows). If
  segment-level facts are ever added, the natural key needs a `dimensions` column
  or they will silently collapse.
- `init.sql` only runs when `pgdata` is empty. Schema changes need
  `docker compose down -v`, or the first real migration file (no migration tool yet).
- `uv sync` installs into `/app/.venv`; the Dockerfile puts it on `PATH`. Local
  deps are in `fundamental-data-feed/.venv`.
- `upsert_concepts` reads the whole `concepts` table deliberately — it is small
  shared reference data, and it avoids tracking newly-created ids.

## Definition of done, per feature

Every feature is graded against six dimensions: engineering quality, system
design, AI integration, product judgment, scope, and communication. So:

1. It maps to a numbered requirement above. If it doesn't, ask why it exists.
2. It works from a clean `docker compose up` with no manual steps and no live
   API dependency (snapshot path implemented for any new source).
3. It is configurable — no ticker, path, credential, or model name in code.
4. Re-running is safe (idempotent) and failures are isolated.
5. README/writeup updated in the same change, not deferred.
6. A test covers the part that could silently be wrong: metric math,
   normalization mapping, snapshot round-trip, router choice. Skip tests for
   trivial getters.
7. `AGENTS.md` is updated: status block, env var table, data model, and gotchas
   all still match what the code does.

## Acceptance test

The six questions in the brief are the real acceptance test. Before calling the
service done, each must work end to end:

1. "What were NVDA's revenue and net income for the last three fiscal years?" (structured)
2. "Which of the five companies had the highest gross margin last year?" (derived, comparative)
3. "What is AAPL's trailing P/E right now?" (cross-source join)
4. "What new risk factors did NVDA add in its latest 10-K versus the prior year?" (filing text)
5. "How did MSFT's revenue grow last year, and what did management attribute it to?" (numbers + MD&A)
6. "What is the company's forward guidance for next quarter?" (must decline — no invention)

## Scope discipline

The brief allots 8–10 hours and rewards deliberate cuts over feature count.
Prefer a working, honest prototype with documented gaps. When you cut something,
record it in the writeup — the reviewers explicitly ask what you left out and why.
