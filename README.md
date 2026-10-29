# Fundamentals Tracker

A Postgres-backed service that ingests three public sources — SEC EDGAR XBRL facts,
SEC 10-K narrative, and Yahoo Finance prices — and answers questions that span the
reported numbers and the prose in the filings.

## Quick start

```bash
cp .env.example .env          # set EDGAR_IDENTITY and the LLM config
docker compose up -d --build
```
The stack comes up on `http://localhost:8000`. Interactive API docs: `/docs`.

## Configuration

All configuration is environment variables; `.env` is read by compose.

| Variable | Purpose |
| --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_PORT` | database |
| `EDGAR_IDENTITY` | `"Name email@example.com"`; SEC returns 403 without it |
| `TICKERS` | tracked universe, comma separated |
| `POLL_INTERVAL_SECONDS` | feed cycle interval (default 3600) |
| `RUN_ONCE` | single pass then exit |
| `SNAPSHOT_DIR` / `SEED_FROM_SNAPSHOT` | seed an empty database from the committed dump |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | used by `POST /ask` |

### Pointing at your own model

`POST /ask` talks to any OpenAI-compatible `/chat/completions` endpoint:

```bash
LLM_BASE_URL=https://your-proxy/v1
LLM_API_KEY=...
LLM_MODEL=your-model-name
```

`LLM_BASE_URL` should stop at the API root — the client appends `/chat/completions`.
Without a base URL and model, `/ask` returns 503 rather than guessing.

**Provider and model used here:** Google AI Studio (Gemini API),
`gemini-3.5-flash-lite`, via the OpenAI-compatible endpoint
`https://generativelanguage.googleapis.com/v1beta/openai`.

## Example interactions

### API calls

Annual metrics, newest first:

```bash
curl -s "localhost:8000/entities/AAPL/metrics?years=2" | jq '.annual[] | {fiscal_year, revenue, gross_margin}'
```

```json
{"fiscal_year": 2025, "revenue": 416161000000.0, "gross_margin": 0.4691}
{"fiscal_year": 2024, "revenue": 391035000000.0, "gross_margin": 0.4621}
```

The cross-source join, with every input and its as-of date:

```bash
curl -s localhost:8000/entities/AAPL/valuation | jq
```

```json
{
  "ticker": "AAPL",
  "price_date": "2026-10-28",
  "price": 343.83,
  "fiscal_year": 2025,
  "period_end": "2025-09-27",
  "eps_diluted": 7.46,
  "shares_outstanding": 14594180000,
  "shares_as_of": "2026-07-17",
  "market_cap": 5017916713433,
  "trailing_pe": 46.09,
  "trailing_ps": 12.06
}
```

### Natural-language queries

`POST /ask` returns the answer, the tools the model chose, citations, and a
grounding check on the numbers.

**Cross-source join**

```bash
curl -s -X POST localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question":"What is AAPL'"'"'s trailing P/E right now?"}' | jq
```

```json
{
  "answer": "Apple Inc. (AAPL) has a trailing P/E of 46.09, as of the price date October 28, 2026 (based on fiscal year 2025 results ending September 27, 2025).",
  "refused": false,
  "tools_used": ["get_valuation"],
  "citations": [{"ticker": "AAPL", "period_end": "2025-09-27", "accession": null}],
  "grounding": {"ok": true, "unverified_numbers": []}
}
```

**Comparative, ranked in SQL rather than by the model**

```bash
curl -s -X POST localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question":"Which of the five companies had the highest gross margin last year?"}' | jq -c '{tools_used, answer}'
```

```json
{
  "tools_used": ["compare_fundamentals"],
  "answer": "NVIDIA (NVDA) had the highest gross margin in its latest reported fiscal year (2026), with a gross margin of 71.07%."
}
```

**Cross-modal: numbers plus filing text**

```bash
curl -s -X POST localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question":"How did MSFT'"'"'s revenue grow last year, and what did management attribute it to?"}' | jq -c '{tools_used, citations: [.citations[] | {section, period_end}]}'
```

```json
{
  "tools_used": ["get_fundamentals", "search_filings", "search_filings", "search_filings"],
  "citations": [{"section": null, "period_end": "2026-06-30"}, {"section": "mda", "period_end": "2025-06-30"}]
}
```

**Out of scope, and it says so**

```json
{
  "answer": "I can only answer from reported fundamentals and the text of the latest 10-K filings. Forward-looking questions such as guidance, forecasts, or price targets are out of scope.",
  "refused": true,
  "tools_used": [],
  "grounding": {"ok": true, "unverified_numbers": []}
}
```
## API

| Route | Returns |
| --- | --- |
| `GET /entities` | tracked companies with fact counts |
| `GET /entities/{ticker}` | one company |
| `GET /entities/{ticker}/concepts` | concept catalog with coverage dates |
| `GET /entities/{ticker}/facts` | raw facts; filters `concept`, `taxonomy`, `form_type`, `start_date`, `end_date`, `limit`, `offset` |
| `GET /entities/{ticker}/facts/{concept}` | time series for one tag |
| `GET /entities/{ticker}/metrics` | annual reported and derived metrics; `years` defaults to 5 |
| `GET /entities/{ticker}/prices` | daily bars, newest first |
| `GET /entities/{ticker}/valuation` | trailing P/E and P/S with as-of dates |
| `GET /entities/{ticker}/filings` | stored narrative sections with chunk counts |
| `GET /entities/{ticker}/filings/{section}` | one whole section (`risk_factors` or `mda`) |
| `GET /narrative/search` | ranked full-text search over filing chunks |
| `POST /ask` | natural-language question routed through tools |

## The agent

Five tools, chosen so the model never does arithmetic:

| Tool | Returns |
| --- | --- |
| `get_fundamentals` | revenue, income, EPS, margins, growth by fiscal year |
| `compare_fundamentals` | companies ranked by one metric |
| `get_valuation` | trailing P/E and P/S with as-of dates |
| `search_filings` | ranked 10-K passages with accession numbers |
| `diff_filing_section` | paragraphs added since the prior year's filing |

The model picks the tool and writes the answer. Rankings, margins, multiples and
the year-over-year paragraph diff are computed in SQL or Python. Every figure in
the answer is then checked against what the tools returned; `grounding.ok` is false
if one cannot be traced.

## Tests

```bash
cd server && uv run pytest              # 66 tests
cd fundamental-data-feed && uv run pytest   # 45 tests
```

None need a database or a network. The tool loop is tested with a fake chat
function, so agent behaviour is verifiable without an API key.

