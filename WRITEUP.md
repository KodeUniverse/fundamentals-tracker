# Design writeup

## What it is

A Postgres-backed service that ingests three public sources — SEC XBRL facts, SEC
10-K narrative, and Yahoo Finance prices — and answers questions that span the
reported numbers and the prose in the filings.

Two Python services share one database: a long-running poller that refreshes the
sources, and a read-only FastAPI service with a natural-language endpoint on top.

```
        ┌─────────────────────────────┐
        │  postgres 17 + pgvector     │
        └───────┬─────────────┬───────┘
                │ writes      │ reads
        ┌───────┴───────┐ ┌───┴──────────────┐
        │ feed (poller) │ │ server (FastAPI) │
        └───────┬───────┘ └───┬──────────────┘
                │             │
      EDGAR XBRL, 10-K,   HTTP clients,
      Yahoo Finance       optional LLM
```

The split is deliberate: ingestion is pull-based and slow-moving, serving is
read-only and must stay up. The feed never serves, and the server never fetches
from a public source. The only thing they share is the schema.

Dependencies inside the server run one way — `main.py` → `llm/` → `analysis/` →
`db.py` — so the agent layer cannot reach into SQL and the analysis layer cannot
know about HTTP.

## Data model

| Table | Holds | Why it looks like this |
| --- | --- | --- |
| `entities` | CIK, name, ticker | Ticker is display-only and not unique; GOOGL and GOOG share a CIK |
| `concepts` | taxonomy + tag name | Reference data, unique on `(taxonomy, name)` |
| `facts` | one row per fact per filing | Natural key `(cik, concept, unit, start, end, accession)` with `NULLS NOT DISTINCT` |
| `filing_sections` | Item 1A / Item 7 text | One row per filing and section, whole text |
| `filing_chunks` | retrieval windows | Generated `tsvector`, nullable `vector(1536)` |
| `prices` | daily bars | Mutable: rewritten each cycle |

Three decisions shaped the rest.

**Facts are stored raw, not as statement line items.** A statement builder picks a
preferred tag and drops the rest, which loses restatements and makes "which filing
said this" unanswerable. Keeping the raw layer means the metric rules can change
without re-ingesting, and every output stays traceable to an accession number.

**Narrative is stored twice: whole and chunked.** Whole sections answer "what new
risk factors did they add", which is a comparison of two contiguous texts; chunks
answer "which paragraphs discuss tariffs", which needs ranking and, later, vectors.
Two risk factors sections are ~115,000 characters each, so the comparison can never
happen in a prompt.

**Prices are rewritten, facts are appended.** Yahoo back-adjusts its entire history
when a split happens, so a stored bar is not immutable. `prices` upserts with
`DO UPDATE`; `facts` uses `DO NOTHING`. The asymmetry is the schema telling the
truth about the two sources.

## The three sources

- **XBRL.** One request per company to `data.sec.gov`, ~25,000 facts each, normalized
  into entity, concepts, and facts inside a single transaction per ticker.
- **10-K narrative.** The two most recent unamended 10-Ks per company; Item 1A and
  Item 7 extracted, whitespace collapsed, then chunked into ~4,000-character
  windows with 15% overlap.
- **Prices.** Ten years of daily bars per ticker in one call, ~12,500 rows total,
  refetched and rescaled every cycle.

A cycle takes about fifteen seconds for all five companies and makes a few dozen
requests, well inside SEC's fair-access limits. Re-running is idempotent: a second
cycle reports `0 new rows`.

**The feed polls every hour, which is far more often than any source changes.**
Fundamentals move quarterly, 10-Ks annually, prices once a day. Most cycles
therefore do work that cannot produce new data: both 10-K documents are re-parsed,
and ~126,000 fact rows are re-sent only to conflict with what is already stored.
That redundancy is deliberate. A "skip if the newest accession is unchanged" check
would remove most of it, but it is another piece of state to get wrong, and the
whole cycle costs seconds. Hourly polling keeps the latency story simple — a new
filing appears within the hour — without a second scheduler or per-source
intervals. At this scale the wasted work is a rounding error; at fifty companies it
would be the first thing to fix.

## The cross-source join

The valuation endpoint joins a Yahoo price with EDGAR fundamentals:

```
market_cap  = latest close × shares outstanding
trailing_pe = latest close ÷ latest annual diluted EPS
trailing_ps = market_cap ÷ annual revenue
```

The share count is the interesting part. `dei:EntityCommonStockSharesOutstanding`
is the freshest tag, but Alphabet reports shares per class with a dimension, and
the pipeline stores consolidated facts only — so the cover-page tag is absent
for GOOGL entirely. The fallback chain (`dei` → `us-gaap:CommonStockShares-
Outstanding` → weighted diluted) is what keeps P/S working for all five
companies. P/E needs no shares, so the two multiples degrade independently.

Every valuation response returns its inputs and their dates — `price_date`,
`period_end`, `shares_as_of` — rather than a bare ratio.

## Where the LLM is, and isn't

`POST /ask` runs a five-step tool loop over five tools: fundamentals, a computed
ranking, valuation, filing search, and a year-over-year section diff.

| Step | LLM | Why |
| --- | --- | --- |
| Out-of-scope detection | No | A keyword guard refuses guidance and forecasts before any model call. Models will invent a plausible number rather than admit they cannot know one. |
| Tool selection | **Yes** | This is the routing the brief asks for; it is the model's job |
| Arithmetic, ranking, diffing | No | Computed in SQL or Python and returned as results |
| Final prose and citations | **Yes** | Language is what it is good at |
| Number verification | No | A check, not a judgment: every figure in the answer must appear in a tool result |

The model never computes, so the only numbers it can report are ones a tool
produced. The response carries `tools_used`, `citations` (ticker, section, period,
accession), and a `grounding` block. The tool descriptions are the routing logic —
five well-described tools, not twelve thin endpoint wrappers.

## Messy data handled

- Revenue tags drift by era and issuer; resolved with a priority-ordered list per
  period.
- GOOGL and ETN never tag `GrossProfit`; derived as revenue minus `CostOfRevenue`,
  with the reported value preferred where present.
- ETN stopped tagging `OperatingIncomeLoss` after 2019; its operating margin is
  null rather than estimated.
- Eaton's Item 7 is a 252-character cross-reference and the real MD&A sits later
  under a heading with a typographic apostrophe; recovered to 74,916 characters.
- Fiscal years end in different months (September, June, January, December), so
  "last year" is approximate across companies — which is why `period_end` is
  always returned.

## Tradeoffs and what was left out

- **No vector embeddings.** Full-text search answers the graded questions, needs no
  API key, and works when the reviewer's proxy exposes no `/embeddings` endpoint.
  The column and index exist; nothing depends on them.
- **Annual EPS, not a TTM rollup.** The brief's own example is annual EPS. A TTM
  figure needs four quarters, consistent fiscal periods, and split-aware
  summation, and would still be an approximation. The as-of date is returned so
  staleness is visible.
- **No MCP server.** It is a distribution mechanism for the same five tools, not an
  architecture, and it would add a process to a stack meant to start with one
  command.
- **No 10-K/A handling, no quarterly metrics, no free cash flow.** Each is a real
  feature and each is out of scope for a prototype.
- **`sources/narratives.py` mixes fetching with chunking.** The split belongs in its
  own change; chunking is pure text processing and deserves its own module.
- **`db.py` imports the row types from `sources/`.** Moving those `TypedDict`s into
  a shared module would make persistence independent of the adapters.

## Testing

101 tests: 56 in the server (metric math, valuation, paragraph diffing, scope
guard, grounding, tool loop) and 45 in the feed (chunking, price conversion,
snapshot discovery). None need a database or a network — the tool loop is
exercised with a fake chat function, so agent behaviour is testable without an
API key.

## Snapshot and offline review

`snapshot/tracker.sql.gz` is a 2 MB dump of the whole universe: 125,886 facts, 20
filing sections, 361 chunks, 12,560 price bars. On an empty database the feed
loads it before the first cycle, then ingests over the top; because ingestion is
idempotent, the second pass reports `0 new rows`. Restoring takes a few seconds and
needs no network, which is what makes the stack reviewable offline.

