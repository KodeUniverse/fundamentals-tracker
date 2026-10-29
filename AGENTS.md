# AGENTS.md

Operating guide for AI agents working in this repo. Read this before touching code.

This file is the shared context for every session. Treat it as authoritative, and
keep it current: when a feature lands, a table or endpoint or env var changes, or a
plan gets abandoned, update the matching section plus **Current status** before you
finish. A stale status block makes the next session rebuild things that already
exist, so keeping it accurate is part of the work, not cleanup afterwards.

## What this project is

"Fundamentals Tracker" — a backend service that tracks company fundamentals from
three public sources and answers natural-language questions spanning both the
reported numbers and the narrative in SEC filings.

Default universe: NVDA, MSFT, AAPL, GOOGL, ETN. It comes from configuration
(`TICKERS`), never hardcoded in code.

## Notes.md

`Notes.md` is the decision log: the reasons behind the design, what was cut and
why, edge cases handled or knowingly skipped, and the numbers worth quoting. It is
not code documentation.

Update it in the same change whenever a decision settles, a feature is cut, or a
data quirk turns up. The reasoning is the part that gets lost first, and it costs
nothing to record while it is fresh. Keep the tone plain and honest — known gaps
belong in the file, not only the wins.

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
7. The repo also includes a README (one-command run, env vars, provider and exact
   model, example interactions) and a design writeup in `WRITEUP.md`.

## Architecture

| Service | Container | Notes |
| --- | --- | --- |
| `db` | `fundamentals-tracker-db` | pgvector/pgvector:pg17 (Debian, extension preinstalled), port 5432, volume `pgdata`, healthcheck |
| `fundamental-data-feed` | `fundamentals-tracker-feed` | Long-running ingest loop, polls `POLL_INTERVAL_SECONDS` |
| `server` | `fundamentals-tracker-server` | FastAPI on port 8000, read-only JSON over Postgres |

Shared `app-net` bridge network. Compose project name: `fundamentals-tracker`.

```
database/init.sql                  schema, applied only on an EMPTY pgdata volume
snapshot/tracker.sql.gz            committed dump, loaded into an empty database
fundamental-data-feed/
  main.py                          env config, cycle loop, per-ticker transaction
  db.py                            psycopg3 persistence (upsert + bulk insert)
  rows.py                          row shapes shared by adapters and persistence
  chunking.py                      section text -> overlapping retrieval windows
  snapshot.py                      seed an empty database from the committed dump
  sources/
    facts.py                       EDGAR XBRL fetch + normalize
    narratives.py                  10-K Item 1A + Item 7 fetch and cleanup
    prices.py                      Yahoo Finance bars
  tests/                           pytest suite for chunking, narratives, prices, snapshot
server/
  main.py                          FastAPI routes and response models
  db.py                            connection pool and all SQL
  analysis/
    metrics.py                     reported and derived metric math
    valuation.py                   cross-source valuation, shared by route and tools
    narrative.py                   paragraph diff for year-over-year sections
  llm/
    agent.py                       scope guard, tool loop, grounding check
    tool_defs.py                   tool schemas and ToolResult
    tools.py                       tool implementations over the database
  tests/                           pytest suite for metrics, valuation, diff, agent
scripts/inspect_db.sh              psql wrapper for the running container
scripts/snapshot.sh                dump the database to snapshot/
scripts/restore_snapshot.sh        load that dump back
test_llm.sh                        exercise the LLM endpoint without the agent
WRITEUP.md                         design writeup
Notes.md                           decisions, cuts, and known gaps
```

Module boundary rule: nothing under `sources/` imports `db`, `db.py` never imports
`edgar`, and no SQL ever lives in `main.py` or in the server's request handlers.
Row shapes live in `rows.py`, so the adapters and the persistence layer share
types without depending on each other. The server depends one way only: `llm/` →
`analysis/` → `db.py`, with `main.py` wiring them together.

## API

Read-only JSON; interactive docs at `/docs`.

| Route | Returns |
| --- | --- |
| `GET /entities` | tracked companies with fact counts |
| `GET /entities/{ticker}` | one company |
| `GET /entities/{ticker}/concepts` | concept catalog with coverage dates |
| `GET /entities/{ticker}/facts` | raw facts; filters `concept`, `taxonomy`, `form_type`, `start_date`, `end_date`, `limit`, `offset` |
| `GET /entities/{ticker}/facts/{concept}` | time series for one tag; `taxonomy` defaults to `us-gaap` |
| `GET /entities/{ticker}/metrics` | annual reported and derived metrics; `years` defaults to 5 |
| `GET /entities/{ticker}/prices` | daily bars, newest first; filters `start_date`, `end_date`, `limit`, `offset` |
| `GET /entities/{ticker}/valuation` | trailing P/E and P/S, with the price date and annual period returned |
| `GET /entities/{ticker}/filings` | stored narrative sections with chunk counts, newest first |
| `GET /entities/{ticker}/filings/{section}` | one whole section (`risk_factors` or `mda`); `year` defaults to the latest filing |
| `GET /narrative/search` | ranked full-text search over chunks; `q`, optional `ticker`, `section`, `year`, `limit` |
| `POST /ask` | natural-language question; returns the answer, tools used, citations, and a grounding check |

Money is in USD as reported, margins and growth are fractions (0.469 = 46.9%),
and a tag the company never reported comes back null rather than estimated.

Narrative search uses Postgres full-text, so it needs no LLM credentials and no
embeddings. `q` accepts plain words, `"quoted phrases"`, and `OR`.

## Data model (database/init.sql)

- `entities(cik BIGINT PK, entity_name, ticker)` — ticker is display-only, NOT
  unique (GOOGL/GOOG share a CIK).
- `concepts(id, taxonomy, name, label, description)` — UNIQUE (taxonomy, name).
  Local name only, e.g. `RevenueFromContractWithCustomerExcludingAssessedTax`.
- `facts(cik, concept_id, unit, value NUMERIC, start_date, end_date, form_type,
  filed_date, accession, filing_fy, filing_fp, frame)` with natural key
  `UNIQUE NULLS NOT DISTINCT (cik, concept_id, unit, start_date, end_date, accession)`.
- `prices(cik, trade_date, close NUMERIC, adj_close NUMERIC, volume, fetched_at)`
  with `UNIQUE (cik, trade_date)`. Ten years of daily bars per company.
- `filing_sections(id, cik, accession, form_type, filed_date, period_end, section,
  content, char_count)` — UNIQUE (cik, accession, section); `section` is
  `risk_factors` (Item 1A) or `mda` (Item 7). The whole section is stored, not
  only its chunks, because year-over-year comparison needs contiguous text.
- `filing_chunks(id, section_id, chunk_index, content, token_count, embedding
  vector(1536), search)` — UNIQUE (section_id, chunk_index). Overlapping ~4000
  character windows; `search` is a generated `tsvector`, `embedding` is NULL
  until an embedding pass fills it.

Semantics that matter:

- `start_date IS NULL` for instant (balance-sheet) facts — ~41% of rows.
- `filing_fy`/`filing_fp` describe the FILING, not the fact's period. An FY2023
  fact reported in the FY2025 10-K carries `filing_fy = 2025`.
- Restatements are preserved as separate rows by `accession`. Do not collapse them.
- `NULLS NOT DISTINCT` is load-bearing for the natural key. Do not remove it.
- `filing_chunks.embedding` is deliberately nullable: full-text retrieval must
  work with no LLM credentials at all. Only fill vectors when an embedding
  endpoint is configured, and never make ingestion depend on one.
- Vector dimension is fixed at 1536 in the column type; changing embedding
  models means an `ALTER TABLE` plus a re-embed.
- `prices` rows are rewritten every cycle, unlike facts. Yahoo back-adjusts its
  whole history when a split happens, so the stored window is only internally
  consistent if it is refreshed at the current scale. `close` is split-adjusted
  and not dividend-adjusted; `adj_close` includes dividends.
- `sources/prices.py` filters `Pandas4Warning` at import. The filter must stay
  *after* `import yfinance`: pandas registers its own warning filters when it is
  imported, and `filterwarnings` prepends, so installing it earlier silently does
  nothing. Delete the block once yfinance stops using `inplace=`.
- Share counts need a tag fallback: Alphabet reports shares per class with a
  dimension, which `get_all_facts()` drops, so `dei:EntityCommonStockShares-
  Outstanding` is absent for GOOGL and `us-gaap:CommonStockSharesOutstanding` is
  the only source. P/E needs no shares; only P/S degrades to null without them.
- `/valuation` uses the latest ANNUAL EPS, not a trailing-twelve-month rollup, and
  returns `period_end` so the staleness is visible. A negative EPS returns a null
  P/E rather than a negative multiple.
- `POST /ask` refuses forward-looking questions with a keyword regex before any
  model call. That is deliberate: models will produce a plausible guidance number
  rather than admit they cannot know one. The keyword list is a demo guard, not a
  classifier — a paraphrase that avoids the words reaches the model, which is why
  the system prompt also states the boundary.
- "forward" is matched only in front of a metric (`forward P/E`, `forward EPS`).
  Bare "forward" would refuse a legitimate question about the forward-looking
  statements inside a 10-K. The first version of the guard let `forward P/E`
  through; the test suite now has both cases.
- The model never computes. Tools return margins, growth, multiples, rankings and
  paragraph diffs already calculated, so the only numbers in an answer are ones a
  tool produced. `grounding` re-checks that with a 1% tolerance, strips
  `10-K`-style form names before extracting figures, and accepts a ratio written
  as a percentage (0.7107 in the payload, "71.07%" in the answer).
- Tool failures (unknown ticker, malformed arguments) are returned to the model as
  an error payload, not raised. It can correct itself once; the step budget stops
  a loop.
- The snapshot is loaded only when `facts` is empty, so a partially populated
  database is left alone instead of being overwritten. The dump is made with
  `--clean`, so `scripts/restore_snapshot.sh` replaces the schema — it is a reset
  tool, not a merge.
- The emptiness check runs inside its own transaction. An open transaction holds a
  lock on `facts`, and the dump's `DROP TABLE` then blocks forever — which is
  exactly what happened the first time the loader ran.

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

The bar: a reader should be able to read a file top to bottom and understand it
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

# inspect the database (no psql needed on the host)
scripts/inspect_db.sh                                  # interactive session
scripts/inspect_db.sh "SELECT e.ticker, count(*) FROM facts f
  JOIN entities e USING (cik) GROUP BY 1;"             # one query

# snapshot: dump after an ingest, restore to get back to a known state
scripts/snapshot.sh                                    # writes snapshot/tracker.sql.gz
scripts/restore_snapshot.sh                            # replaces the database

# local run (deps live in fundamental-data-feed/.venv)
fundamental-data-feed/.venv/bin/python fundamental-data-feed/main.py

# server tests: metrics, valuation, diff, scope guard, tool loop (no database, no network)
cd server && uv run pytest

# feed tests: chunking and price conversion (needs edgartools and pandas)
cd fundamental-data-feed && uv run pytest
```

`RUN_ONCE=1` exits after one cycle — use it for tests, snapshot builds, and CI.

## Environment variables

| Var | Purpose |
| --- | --- |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` / `_PORT` | database |
| `DATABASE_URL` | libpq URL used by both services, e.g. `postgresql://user:pass@db:5432/tracker` |
| `EDGAR_IDENTITY` | "Name email@example.com" — SEC returns 403 without it |
| `TICKERS` | comma-separated universe |
| `POLL_INTERVAL_SECONDS` | feed cycle interval (default 3600) |
| `RUN_ONCE` | single pass then exit |
| `LOG_LEVEL` | default INFO |
| `SNAPSHOT_DIR` / `SEED_FROM_SNAPSHOT` | when enabled and `facts` is empty, the feed loads `<SNAPSHOT_DIR>/tracker.sql.gz` before its first cycle; live ingestion then upserts over it |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | used by `POST /ask`; any OpenAI-compatible `/chat/completions` endpoint. Without base URL and model, `/ask` returns 503 |

Read env at the entrypoint and pass values down; don't sprinkle `os.getenv`
through library functions. (`db.connect()` reading `DATABASE_URL` is the
deliberate exception — it is the one place a connection is opened.)

## Current status (last verified 2026-10-29)

Update this block as part of any change that moves the project forward: a new
source, table, endpoint, or env var, a decision that is now settled, or work that
was dropped. Bump the date. If another section of this file stops matching the
code, fix the file instead of working around it.

Staleness check before finishing — if a line matches but the feature exists, these
files are stale:

```bash
grep -in "not built\|not started\|no snapshot\|no prices" Notes.md AGENTS.md
```

Done:
- XBRL ingestion, live, for all five tickers: 125,545 facts, 1,350 concepts.
- Idempotent re-ingestion, restatement preservation, graceful SIGTERM.
- Containerized feed running as a long-lived poller.
- 10-K narrative ingest: Item 1A and Item 7 for the latest two filings per
  company, 20 sections and 361 chunks, stored whole plus chunked for retrieval.
- Market data: ten years of daily bars per company from Yahoo Finance, 12,560
  rows, refetched and rescaled each cycle.
- Snapshot: a 2 MB committed dump. An empty database is seeded from it before the
  first cycle, so the stack starts and serves with no network.
- Trailing valuation: P/E on latest annual diluted EPS and P/S on market cap
  over annual revenue, with price date and period end returned.
- Agent layer on `POST /ask`: deterministic scope guard, OpenAI-compatible tool
  loop over five computed tools, citations, and a grounding check on the numbers.
- Postgres full-text retrieval over chunks works with no LLM credentials.
- Read API on port 8000: entities, concept catalog, filtered raw facts,
  per-concept time series, annual metrics with margins and YoY growth, prices,
  valuation, narrative sections, ranked narrative search, natural-language ask.
- 66 pytest tests covering metric and valuation math, paragraph diffing, the
  scope guard, grounding, and the tool loop (`server/tests`), plus 45 covering
  chunking, price conversion, and snapshot discovery (`fundamental-data-feed/tests`).
- README with the one-command run, env vars, the exact model, and real example
  interactions captured from a running stack.

Not started: embedding pass (`filing_chunks.embedding` is NULL everywhere).

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
- Gross profit is not tagged by every issuer. GOOGL and ETN have no `GrossProfit`,
  so `metrics.py` derives it as revenue minus `CostOfRevenue`. ETN stopped tagging
  `OperatingIncomeLoss` after 2019, so its operating margin is null for recent
  years. That is the data, not a bug — do not fill the gaps with estimates.
- Eaton's Item 7 is a 252-character cross-reference, not the MD&A. The real text
  sits later in the document under an unnumbered heading, and that heading uses a
  typographic apostrophe, so a straight-quote pattern misses it. `narratives.py`
  normalizes quotes and takes the last heading hit when the parsed section looks
  like a stub. edgartools logs the same truncation warning, so the fallback is
  not fighting the parser blindly.
- `filing_sections.content` is whitespace-collapsed on purpose. EDGAR's
  HTML-to-text pads table cells with runs of spaces, which would otherwise
  dominate chunk boundaries and the `tsvector`.
- The pgvector image runs `init.sql` with `ON_ERROR_STOP=1`, so any failing
  statement aborts the whole schema. That is how the pre-existing
  `COMMENT ON TABLE concepts.taxonomies` (a table that never existed) got
  exposed; it is now a column comment.
- Each `docker compose build` of the feed leaves a ~2.5 GB dangling image, and the
  root filesystem here is only 125 GB. If builds fail with "No space left on
  device", run `docker image prune -f` (dangling only) before touching anything else.

## Definition of done, per feature

A feature is done when all of the following hold:

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
8. `Notes.md` is updated when the change settles a decision, takes a cut, or
   surfaces an edge case — with the reason, not just the outcome.

## Acceptance test

Before calling the service done, each of these must work end to end:

1. "What were NVDA's revenue and net income for the last three fiscal years?" (structured)
2. "Which of the five companies had the highest gross margin last year?" (derived, comparative)
3. "What is AAPL's trailing P/E right now?" (cross-source join)
4. "What new risk factors did NVDA add in its latest 10-K versus the prior year?" (filing text)
5. "How did MSFT's revenue grow last year, and what did management attribute it to?" (numbers + MD&A)
6. "What is the company's forward guidance for next quarter?" (must decline — no invention)

## Scope discipline

Deliberate cuts beat feature count. Prefer a working, honest prototype with
documented gaps over a broad half-finished one. When you cut something, record it
in the writeup: what was left out and why is part of the design, not an apology.
