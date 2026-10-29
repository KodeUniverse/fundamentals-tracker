# Notes

Last verified: 2026-10-28

Working notes for the case study writeup and the follow-up interview. This is not
documentation of how the code works — that is `README.md` and `AGENTS.md`. This
file records why things are the way they are, what was cut, and what I know is
still imperfect.

Keep it current: when a decision is made, a feature is cut, or an edge case shows
up, add it here in the same change.

As features land, these sections move rather than only grow:

- "What I chose not to build" — an item leaves the table when it ships.
- "If asked what I'd do next" — the finished item comes off the list.
- "Honest gaps" — the gap is removed when it is closed.
- "Ambiguities in the brief" — a question becomes a decision once it is settled.
- "Numbers worth quoting" — add new figures, keep the verified ones.
- The EDGAR edge cases stay. They are properties of the source, not of the build.

## The 60-second version

A Postgres-backed service that ingests three public sources — EDGAR XBRL company
facts, EDGAR 10-K narrative (Item 1A and Item 7), and market data — and serves
them through read-only HTTP endpoints plus a retrieval layer over filing text.

The deliberate shape: store facts exactly as reported, resolve metrics above the
storage layer, and keep every answer traceable to an accession number. Nothing is
estimated. When a tag is missing, the field is null and the reason is documented.

## What I chose not to build

| Not built | Why | What it would take |
| --- | --- | --- |
| Vector embeddings | The graded questions are lexical ("partially offset by", "driven by") and the reviewer's LLM proxy may not expose `/embeddings`. Full-text works with zero credentials. | Embedding pass per chunk, RRF fusion with `ts_rank`. Column and HNSW index already exist. |
| True TTM multiples | The brief's own example is "latest reported annual EPS times current price". A TTM rollup needs four quarters, consistent fiscal periods, and split-aware summation. | TTM net income or EPS assembly from quarterly facts. |
| Year-over-year risk factor diff endpoint | The data is already there; an LLM can fetch both sections and compare. A bespoke diff is a UI feature, not a data one. | Alignment + set difference over paragraphs. |
| Segment-level facts | `get_all_facts()` returns consolidated only. Adding segments needs a `dimensions` column in the natural key or rows silently collapse. | Schema change + dimension parsing. |
| 10-K/A handling | Amendment text would replace the original narrative. The brief asks for annual 10-Ks. | `amendments=True` plus a version column. |
| Retention policy | Nothing is deleted. `head(2)` is a fetch window, not a storage bound, so history accumulates. | Explicit delete or partition by period. |
| Async API | psycopg is blocking; `async def` would freeze the event loop. Measured below. | Async pool + full rewrite of the DB layer. |
| Auth | Read-only, local demo. | Out of scope. |
| MCP server | The same five tools would work behind an MCP wrapper, but it adds a process and a client config to a service the reviewer runs with one command. It is a distribution mechanism, not an architecture. | Thin wrapper over `tool_defs.TOOL_SPECS`. |

## Edge cases found, and what I did

**Eaton's Item 7 is a 252-character cross-reference.** The real MD&A sits later in
the document as an unnumbered section, and its heading uses a typographic
apostrophe (`MANAGEMENT’S`), so a straight-quote match finds the stub instead.
Recovery normalizes quotes, matches the heading, and takes the last hit.
Result: 252 → 74,916 characters (FY2025) and 66,889 (FY2024).
edgartools detects the same thing and logs `below the expected minimum of 11,440
for a 10-K`, but it only warns — it never follows the pointer. That is the whole
reason the fallback exists.

**Revenue tags drift across eras and issuers.** AAPL uses `SalesRevenueNet` until
2017, then `RevenueFromContractWithCustomerExcludingAssessedTax`, with `Revenues`
overlapping both. GOOGL's contract tag stops at FY2024 and `Revenues` covers
FY2025. Handled with a priority-ordered tag list per metric, resolved per period.

**GOOGL and ETN never tag `GrossProfit`.** Derived as revenue − `CostOfRevenue`.
Where `GrossProfit` is reported (AAPL, MSFT, NVDA), the reported value wins.

**ETN stopped tagging `OperatingIncomeLoss` after 2019.** Its operating margin is
null for recent years. Left null on purpose — inventing a number would undermine
every other answer the service gives.

**Restatements.** The same period appears under multiple accessions. Raw endpoints
return every row; `/metrics` picks the most recently filed value per period with
`DISTINCT ON (concept, period_end) ORDER BY filed_date DESC`.

**Instant facts.** ~41% of rows have `start_date IS NULL` (balance sheet). Annual
metrics filter on a 300–400 day duration instead of assuming a start date exists.

**`filing_fy` is the filing's year, not the fact's.** A FY2023 fact reported inside
the FY2025 10-K carries `filing_fy = 2025`. Documented in the schema comment
because it is the kind of thing that produces silently wrong comparisons.

**Fiscal calendars differ.** AAPL ends in September, MSFT in June, NVDA in January,
GOOGL and ETN in December. "Highest gross margin last year" across all five
compares different economic windows. The API returns `period_end` on every metric
row so a reader can see this instead of trusting the label "2025".

**Alphabet has no cover-page share count.** Its `dei:EntityCommonStockShares-
Outstanding` is reported per class with a dimension, and the pipeline stores
consolidated facts only, so the tag vanishes entirely. A single-concept design
would have returned null for one of five companies and silently broken P/S. The
`us-gaap:CommonStockSharesOutstanding` total is the fallback that saves it, with
the freshest period winning and tag order as the tiebreak.

**Splits needed no code.** Yahoo back-adjusts its entire history, so the stored
AAPL series reads ~$125 on both sides of the 2020 4:1 split and NVDA ~$121 across
the 2024 10:1. The only split hazard is mixing rows fetched before and after the
event, which the full-window refetch removes.

**Prices are mutable, facts are not.** Every cycle rewrites the stored bars
because a split rescales history retroactively. Facts are `DO NOTHING` on
conflict; prices are `DO UPDATE`. That asymmetry is deliberate and worth
explaining rather than hiding.

**An open transaction blocked the snapshot load.** The emptiness check ran a
`SELECT` on the feed's long-lived connection, which left a transaction open and
held a lock on `facts`. The dump's `DROP TABLE facts` then waited forever. The
fix is one line — run the check inside `with conn.transaction()` — but it is the
kind of deadlock that only appears when a subprocess touches the same tables as
the parent connection.

**Chunk size overshoot.** The carry-over tail was appended after the size check,
so a 3,995-character paragraph plus overlap produced a 4,296-character chunk.
Found by a synthetic test, fixed by only carrying as much tail as the room allows.

**`init.sql` had a broken statement.** `COMMENT ON TABLE concepts.taxonomies`
referenced a table that never existed. The alpine entrypoint tolerated it; the
pgvector image runs init with `ON_ERROR_STOP=1` and aborted the whole schema,
which is how it surfaced. Now a column comment on `concepts.taxonomy`.

## Edge cases I know about and did not handle

- The Eaton recovery slices from the heading to end of document. Verified that
  nothing but MD&A follows in both filings, but it is an assumption, not a
  guarantee. A company that stubs Item 7 *and* prints later items below the
  unnumbered MD&A would over-capture.
- No monitoring or alerting. A parser regression shows up as a short section in
  `filing_sections`, not as an error.
- `/entities/{ticker}/concepts` has no pagination. ~505 rows per company.
- Metrics are annual only. No quarterly series, no TTM rollup yet.
- All money is assumed USD. No currency column.
- The feed polls; it does not react to EDGAR's index of new filings.

## Design decisions I expect to be asked about

**Why raw facts instead of statement line items?**
Statement builders pick a preferred tag and drop the rest. That loses restatements
and makes "which filing said this" unanswerable. Storing the raw layer keeps
provenance and lets the metric layer change without re-ingesting.

**Why `UNIQUE NULLS NOT DISTINCT`?**
`start_date` is NULL for instant facts. Under normal uniqueness, two identical
balance-sheet facts would both pass the constraint and duplicate on re-ingest.

**Why sync ingestion?**
edgartools is blocking. `ThreadPoolExecutor` would only help if the network were
the bottleneck across tickers; parsing and DB writes are single-threaded on
purpose.

**Why sync FastAPI handlers?**
The DB driver is blocking. `def` handlers run in a thread pool, so requests
overlap; `async def` with psycopg would serialize everything on the event loop.
Measured: 4 ms average, 20 requests at 10-way parallelism in 36 ms. Serial would be
~80 ms. This is a deliberate choice, not an oversight.

**Why two narrative tables instead of one row per company with two columns?**
"Which risk factors did NVDA *add* versus last year" needs both filings present at
once; a one-row-per-company design forces `risk_factors_current` /
`risk_factors_prior` columns, which gets worse every year. And a vector column
holds one vector per row — a 115,000-character section is ~15k tokens, well past
any embedding model's limit, so embeddings have to live per chunk.

**Why is full-text the default and vectors optional?**
Attribution answers are formulaic: "primarily due to", "partially offset by",
"driven by". Those words are the signal. Vector similarity is for when the words
differ, and in an agent loop the model writes the query, so lexical search stays
precise. The nullable `embedding` column keeps the door open without making the
demo depend on an endpoint the reviewer might not have.

**Why poll hourly?**
Fundamentals change quarterly. Hourly is already far more frequent than the source
updates, and costs ~15 SEC requests/hour against a 10 req/s limit, ~5 s of work per
cycle. Long-running poller, one transaction per ticker, per-ticker failure
isolation.

**How do you know the data is live and not seeded?**
`EDGAR_USE_LOCAL_DATA` is unset, so facts are downloaded from `data.sec.gov` each
cycle; the in-memory facts cache holds one CIK, so five tickers evict it constantly.
Every row carries `accession` and `filed_date`. A second cycle logs
`0 new rows (25135 already stored)`, which is only possible if it diffed against
the real source.

**Why psycopg and not an ORM?**
Every query is hand-written and lives in one module. An ORM would add a layer
between the SQL and the reader without removing a line.

**Why yfinance rather than Alpaca or Alpha Vantage?**
It is the brief's first-named source, needs no API key, and returns adjusted
closes. Alpaca and Alpha Vantage would put a credential between the reviewer and
the stack for identical daily data. The known weakness — it is an unofficial
scraper — is covered by per-ticker error isolation and the snapshot, so a Yahoo
failure keeps the last stored window instead of emptying the table.

**Why ten years of bars?**
It spans both recent splits (AAPL 2020, NVDA 2024), so the adjusted series is
visibly continuous. The full fetch is 12,560 rows in under a second; a 30-day
incremental window would be cheaper but would create exactly the split-vintage
mixing problem the full refresh avoids.

**Why annual EPS instead of TTM for P/E?**
The brief's example is "latest reported annual EPS times current price". A TTM
rollup needs four quarters, consistent fiscal periods, and split-aware summation,
and would still be an approximation. The endpoint returns `period_end` and
`price_date`, so a stale annual figure is visible rather than disguised.

**Why is P/E null for a loss-making company?**
A negative multiple is not a valuation signal. Returning `-12.4` would invite the
reader to compare it with a positive one. The API says null and the underlying
EPS is still in the response.

**Why a keyword guard instead of asking the model to decline?**
Because that is a refused-answer problem, not a comprehension problem. A model
asked "what is the guidance for next quarter" will usually produce a plausible
number rather than admit it cannot know one. A regex catches the brief's example
deterministically, costs nothing, and is auditable. The weakness is paraphrase, so
the system prompt states the same boundary — described honestly in the writeup as
a demo guard rather than a classifier.

It also has concrete holes, which is worth admitting: "forward P/E" got through the
first version because "forward" was only matched inside "going forward". The fix
scopes it to a metric (`forward P/E`, `forward EPS`, …) rather than matching the
bare word, so "what forward-looking statements do they disclose in Item 1A" still
reaches the model. Both cases are now tests.

**Why does the model never compute?**
Because arithmetic and ranking are where an otherwise correct answer goes quietly
wrong. `compare_fundamentals` returns a sorted ranking and `diff_filing_section`
returns added paragraphs, both computed in Python. The model reads and explains.
The grounding check then confirms every figure in the prose appeared in a tool
result, with 1% tolerance for rounding.

**Why a diff tool rather than retrieval for "what new risk factors did they add"?**
Two risk-factor sections are ~115,000 characters each. They cannot both fit in a
prompt, and similarity search answers "what is said about X", not "what is new".
Splitting both sections into paragraphs and comparing word sets answers the actual
question, and returns passages a person can check against the filing. It found 30
new NVDA paragraphs between FY2025 and FY2026.

**Why five tools rather than one per endpoint?**
The endpoint list is a UI surface, not a model surface. Models route better with
few, high-level, well-described tools — and the descriptions are the routing logic.
Twelve thin tools would make "which one answers a margin comparison" a coin flip.

**Why `gemini-3.5-flash-lite` and not the newest Flash?**
`gemini-3.8-flash` was capacity-limited during development (503, then hanging
requests), and the free tier allows 20 requests per day *per model* while one
question costs two or three calls. `3.5-flash-lite` answered in 0.59s, called tools
correctly, and had quota available. The model is one environment variable, so the
choice is a deployment detail rather than an architectural one — but the README has
to name exactly what ran, so it says this.

**Why does the agent make only two calls per question?**
Because free tiers are tight. One call picks the tool, one call phrases the answer;
the loop can do more when it needs to, but the common path is two. That budget is
also why ranking and diffing are tools rather than something the model derives: it
keeps the loop short.

**Why poll hourly when fundamentals change quarterly?**
Because the interval was never the expensive part — the full re-read is, and it
costs about fifteen seconds. Hourly keeps "a new filing appears within the hour"
true without a second scheduler, and 120 SEC requests a day is a rounding error
against a 10-per-second limit. The obvious optimisation is skipping work when the
newest accession is unchanged; I left it out as another stateful path to get wrong
at five tickers, and it is the first thing I would add at fifty.

**Why pytest rather than unittest?**
The tests that matter here are tables: which phrasings must be refused, which
metric edge cases return null, which valuation inputs degrade. `parametrize` makes
each case a separate line in the report, so a failure names the input instead of
"one of these four failed". It is a dev-only dependency — the Docker image builds
with `uv sync --no-dev`, so it adds nothing to what the reviewer runs.

**Why `analysis/` and `llm/` rather than a flat server directory?**
Two folders that each answer one question. `analysis/` holds derived values —
metrics, valuation, the paragraph diff — so "where does a number come from" has one
answer. `llm/` holds the agent layer, which is the part a reviewer will want to
read in isolation. The dependency runs one way (`llm/` → `analysis/` → `db.py`), so
neither folder can quietly reach sideways into the other.

**How does the offline snapshot work?**
`scripts/snapshot.sh` dumps the running database to `snapshot/tracker.sql.gz`
(2 MB for the whole universe). The feed checks two env vars at startup: if
`USE_SNAPSHOT_IF_NO_NETWORK` is on and the `facts` table is empty, it streams that
dump through `psql --single-transaction` before the first cycle. Live ingestion then
upserts over it, so the seeded data and the ingested data are the same rows.

The alternative — restoring at Postgres init time — needs no code but no env var
either, and it re-loads even when a developer wants a clean ingest. Loading in the
feed keeps the switch explicit and makes the "did it come from the snapshot or from
EDGAR?" answer visible in the log.

## Numbers worth quoting

- 125,545 facts, 1,350 concepts, 5 companies.
- 20 narrative sections, 361 chunks, none over 4,000 characters.
- AAPL FY2025: revenue 416.161B, net income 112.01B, EPS 7.46, gross margin 46.91%,
  operating margin 31.97%, revenue growth 6.43%.
- GOOGL FY2025: revenue 402.836B, net income 132.17B, EPS 10.81, gross margin 59.65%.
- NVDA FY2026: revenue 215.938B, net income 120.067B, EPS 4.90, gross margin 71.07%.
- ETN FY2025: revenue 27.448B, gross margin 37.59%, operating margin null.
- Eaton MD&A recovery: 252 → 74,916 characters.
- Market data: 12,560 daily bars, 2016-10-31 to 2026-10-28, refreshed each cycle.
- AAPL: $344.91, P/E 46.2, P/S 12.1, market cap 5.03T, shares as of 2026-07-17.
- NVDA: $217.85, P/E 44.5, P/S 24.3, market cap 5.25T.
- MSFT: $529.49, P/E 29.5, P/S 11.9, market cap 3.93T.
- GOOGL: $323.83, P/E 30.0, P/S 9.8, market cap 3.96T.
- ETN: $390.74, P/E 37.4, P/S 5.5, market cap 151.8B.
- API: 4 ms average; 20 requests at 10-way parallelism in 36 ms.
- Ingest: ~5 s per cycle for all five tickers.
- Search for "tariff" across all filings: 40 chunks — NVDA 17, MSFT 7, ETN 7,
  AAPL 6, GOOGL 3.
- Year-over-year diff: 30 new risk-factor paragraphs in NVDA's FY2026 10-K
  against FY2025.
- Agent: 5 tools, 5-step budget, deterministic guard, grounding with 1% tolerance.

## Ambiguities in the brief

- **"Last three fiscal years"** — fiscal year ends differ per company, so a
  calendar-year reading and a fiscal-year reading give different windows. I return
  `fiscal_year` and `period_end` and let the caller see both.
- **"Highest gross margin last year"** — mixed fiscal calendars make "last year"
  approximate across five companies. Documented rather than papered over.
- **"Latest two annual filings"** — unamended 10-Ks only. A 10-K/A is a correction,
  not a new annual report.

## If asked what I'd do next, in order

1. Embeddings, only if the chosen provider exposes `/embeddings`.
2. A second narrative split: `sources/narratives.py` still mixes fetching with
   chunking.
3. TTM multiples instead of annual EPS.

## Honest gaps

- `/ask` needs an LLM endpoint configured; without one it returns 503 rather than
  pretending. No key is committed.
- Google's free tier allows 20 requests per day per model, and a question costs two
  or three, so the demo is quota-bound. The reviewer's own proxy avoids this, which
  is why the model is configuration and not code.
- The scope guard is keyword-based, so a paraphrase without the trigger words
  reaches the model. The system prompt is the second line of defence.
- Grounding is a heuristic: it catches invented figures, and accepts unit rephrasing
  (percent, billions), but it does not verify that a correctly quoted number was
  used to answer the right question.
- P/E uses the latest annual EPS, so it can be up to a year stale for companies
  whose fiscal year ended long ago. `period_end` is returned rather than hidden.
- Share counts can be a quarter behind the price date (`shares_as_of` shows it).
- The Eaton recovery is the only parser workaround, and it has no unit test — it
  runs in production and is verified by inspection of the stored text.
- No schema migration path: `init.sql` runs only on an empty volume, so schema
  changes mean `docker compose down -v` and a re-ingest.
- The snapshot is data plus schema, not a migration mechanism. It is a reset, not
  a way to upgrade a running database.
