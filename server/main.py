"""FastAPI service over the ingested SEC facts.

Endpoints are read-only. Handlers are sync on purpose: psycopg is blocking, and
FastAPI runs sync handlers in a thread pool, so the pool connections are never
shared across concurrent requests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

import db
import metrics


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.pool.open()
    yield
    db.pool.close()


app = FastAPI(
    title="Fundamentals Tracker API",
    version="0.2.0",
    description="SEC XBRL fundamentals for the tracked universe.",
    lifespan=lifespan,
)


class EntitySummary(BaseModel):
    cik: int
    ticker: str | None
    entity_name: str
    fact_count: int


class ConceptSummary(BaseModel):
    taxonomy: str
    name: str
    label: str | None
    fact_count: int
    first_period: date
    last_period: date


class Fact(BaseModel):
    taxonomy: str
    concept: str
    unit: str
    value: float
    start_date: date | None
    end_date: date
    form_type: str
    filed_date: date
    accession: str
    filing_fy: int | None
    filing_fp: str | None


class FactPage(BaseModel):
    ticker: str
    cik: int
    count: int
    limit: int
    offset: int
    facts: list[Fact]


class AnnualMetrics(BaseModel):
    fiscal_year: int
    period_end: date
    revenue: float | None
    gross_profit: float | None
    operating_income: float | None
    net_income: float | None
    eps_diluted: float | None
    gross_margin: float | None
    operating_margin: float | None
    revenue_yoy: float | None
    net_income_yoy: float | None


class MetricsResponse(BaseModel):
    ticker: str
    cik: int
    entity_name: str
    annual: list[AnnualMetrics]


class FilingSectionSummary(BaseModel):
    accession: str
    form_type: str
    filed_date: date
    period_end: date
    section: str
    char_count: int
    chunk_count: int


class FilingSection(BaseModel):
    ticker: str
    cik: int
    accession: str
    form_type: str
    filed_date: date
    period_end: date
    section: str
    content: str
    char_count: int


class NarrativeHit(BaseModel):
    ticker: str | None
    section: str
    period_end: date
    accession: str
    chunk_index: int
    rank: float
    content: str


class NarrativeSearchResponse(BaseModel):
    query: str
    count: int
    hits: list[NarrativeHit]


class PriceBar(BaseModel):
    trade_date: date
    close: float
    adj_close: float | None
    volume: int | None


class PricePage(BaseModel):
    ticker: str
    cik: int
    count: int
    limit: int
    offset: int
    bars: list[PriceBar]


class Valuation(BaseModel):
    ticker: str
    cik: int
    price_date: date
    price: float
    fiscal_year: int
    period_end: date
    eps_diluted: float | None
    revenue: float | None
    net_income: float | None
    shares_outstanding: float | None
    shares_as_of: date | None
    market_cap: float | None
    trailing_pe: float | None
    trailing_ps: float | None


def _entity_or_404(ticker: str) -> dict[str, Any]:
    entity = db.get_entity(ticker.upper())
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker.upper()}")
    return entity


def _fact_page(entity: dict[str, Any], rows: list[dict[str, Any]], limit: int, offset: int) -> FactPage:
    return FactPage(
        ticker=entity["ticker"] or str(entity["cik"]),
        cik=entity["cik"],
        count=len(rows),
        limit=limit,
        offset=offset,
        facts=[Fact(**row) for row in rows],
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "fundamentals-tracker-server", "docs": "/docs"}


@app.get("/entities", response_model=list[EntitySummary], tags=["entities"])
def entities() -> list[dict[str, Any]]:
    """Tracked companies, with how many facts are stored for each."""
    return db.list_entities()


@app.get("/entities/{ticker}", response_model=EntitySummary, tags=["entities"])
def entity(ticker: str) -> dict[str, Any]:
    return _entity_or_404(ticker)


@app.get("/entities/{ticker}/concepts", response_model=list[ConceptSummary], tags=["facts"])
def concepts(ticker: str) -> list[dict[str, Any]]:
    """Every concept reported by this company, with coverage dates."""
    entity = _entity_or_404(ticker)
    return db.list_concepts(entity["cik"])


@app.get("/entities/{ticker}/facts", response_model=FactPage, tags=["facts"])
def facts(
    ticker: str,
    concept: str | None = Query(None, description="Exact tag name, e.g. Revenues"),
    taxonomy: str | None = Query(None, description="e.g. us-gaap, dei"),
    form_type: str | None = Query(None, description="e.g. 10-K, 10-Q"),
    start_date: date | None = Query(None, description="Period end on or after"),
    end_date: date | None = Query(None, description="Period end on or before"),
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> FactPage:
    """Raw facts for a company, newest period first."""
    entity = _entity_or_404(ticker)
    rows = db.list_facts(
        cik=entity["cik"],
        concept=concept,
        taxonomy=taxonomy,
        form_type=form_type,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return _fact_page(entity, rows, limit, offset)


@app.get("/entities/{ticker}/facts/{concept}", response_model=FactPage, tags=["facts"])
def concept_facts(
    ticker: str,
    concept: str,
    taxonomy: str = Query("us-gaap", description="Taxonomy the tag belongs to"),
    form_type: str | None = Query(None, description="e.g. 10-K, 10-Q"),
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> FactPage:
    """Time series for one concept, newest period first.

    Every reported value is returned, including restatements; the `accession`
    field identifies which filing reported each row.
    """
    entity = _entity_or_404(ticker)
    rows = db.list_facts(
        cik=entity["cik"],
        concept=concept,
        taxonomy=taxonomy,
        form_type=form_type,
        limit=limit,
        offset=offset,
    )
    return _fact_page(entity, rows, limit, offset)


@app.get("/entities/{ticker}/metrics", response_model=MetricsResponse, tags=["metrics"])
def annual_metrics(ticker: str, years: int = Query(5, ge=1, le=20)) -> MetricsResponse:
    """Annual reported metrics plus margins and year-over-year growth.

    Margins and growth are fractions (0.469 = 46.9%). Money is in USD as
    reported, EPS in USD per diluted share. Missing tags come back as null
    rather than being estimated.
    """
    entity = _entity_or_404(ticker)
    rows = db.annual_metric_facts(entity["cik"], metrics.ALL_TAGS)
    return MetricsResponse(
        ticker=entity["ticker"] or str(entity["cik"]),
        cik=entity["cik"],
        entity_name=entity["entity_name"],
        annual=metrics.annual_metrics(rows, years),
    )


@app.get("/entities/{ticker}/filings", response_model=list[FilingSectionSummary], tags=["narrative"])
def filings(ticker: str) -> list[dict[str, Any]]:
    """Stored 10-K narrative sections: one row per filing and section.

    Two filings are kept per company, so the latest annual report and the prior
    one can be compared. Bodies are fetched from the section route below.
    """
    entity = _entity_or_404(ticker)
    return db.list_filings(entity["cik"])


@app.get("/entities/{ticker}/filings/{section}", response_model=FilingSection, tags=["narrative"])
def filing_section(
    ticker: str,
    section: Literal["risk_factors", "mda"],
    year: int | None = Query(None, description="Fiscal year of the period end; defaults to the latest filing"),
) -> FilingSection:
    """One whole section, contiguous, newest filing unless `year` is given."""
    entity = _entity_or_404(ticker)
    row = db.get_filing_section(entity["cik"], section, year)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No {section} stored for {ticker} in {year or 'any year'}")
    return FilingSection(ticker=entity["ticker"] or str(entity["cik"]), cik=entity["cik"], **row)


@app.get("/narrative/search", response_model=NarrativeSearchResponse, tags=["narrative"])
def narrative_search(
    q: str = Query(..., min_length=2, description='Plain words, "quoted phrase", or OR'),
    ticker: str | None = Query(None, description="Restrict to one company"),
    section: Literal["risk_factors", "mda"] | None = Query(None),
    year: int | None = Query(None, description="Fiscal year of the period end"),
    limit: int = Query(10, ge=1, le=50),
) -> NarrativeSearchResponse:
    """Full-text search over filing chunks, best match first.

    Postgres full-text, so it works with no LLM credentials and no embeddings.
    Useful for cross-company recall ("which of them discuss tariffs"), which
    keyword matching handles better than a similarity search over five
    differently-worded sections.
    """
    cik = _entity_or_404(ticker)["cik"] if ticker else None
    hits = db.search_chunks(query=q, cik=cik, section=section, year=year, limit=limit)
    return NarrativeSearchResponse(query=q, count=len(hits), hits=hits)


@app.get("/entities/{ticker}/prices", response_model=PricePage, tags=["prices"])
def prices(
    ticker: str,
    start_date: date | None = Query(None, description="Trading date on or after"),
    end_date: date | None = Query(None, description="Trading date on or before"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> PricePage:
    """Daily bars, newest first.

    `close` is split-adjusted and not dividend-adjusted, which is what a market
    cap needs. `adj_close` includes dividends and will differ from it.
    """
    entity = _entity_or_404(ticker)
    rows = db.list_prices(
        cik=entity["cik"],
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return PricePage(
        ticker=entity["ticker"] or str(entity["cik"]),
        cik=entity["cik"],
        count=len(rows),
        limit=limit,
        offset=offset,
        bars=[PriceBar(**row) for row in rows],
    )


@app.get("/entities/{ticker}/valuation", response_model=Valuation, tags=["metrics"])
def entity_valuation(ticker: str) -> Valuation:
    """Trailing P/E and P/S joining the latest close with the latest annual year.

    P/E is price divided by reported annual diluted EPS, as the brief describes a
    trailing multiple. It is not a twelve-month rollup, so `period_end` is
    returned alongside it: an annual figure can be months old, and the number is
    only meaningful with its as-of dates. No forward estimates are involved.
    """
    entity = _entity_or_404(ticker)
    price = db.latest_price(entity["cik"])
    if price is None:
        raise HTTPException(status_code=404, detail=f"No prices stored for {ticker}")

    annual = metrics.annual_metrics(db.annual_metric_facts(entity["cik"], metrics.ALL_TAGS), years=1)
    if not annual:
        raise HTTPException(status_code=404, detail=f"No annual fundamentals stored for {ticker}")

    latest = annual[0]
    shares = metrics.latest_shares(db.share_facts(entity["cik"], metrics.SHARES_TAGS))
    values = metrics.valuation(
        price=price["close"],
        shares=shares[0] if shares else None,
        eps=latest["eps_diluted"],
        revenue=latest["revenue"],
    )

    return Valuation(
        ticker=entity["ticker"] or str(entity["cik"]),
        cik=entity["cik"],
        price_date=price["trade_date"],
        price=price["close"],
        fiscal_year=latest["fiscal_year"],
        period_end=latest["period_end"],
        eps_diluted=latest["eps_diluted"],
        revenue=latest["revenue"],
        net_income=latest["net_income"],
        shares_outstanding=shares[0] if shares else None,
        shares_as_of=shares[1] if shares else None,
        market_cap=values["market_cap"],
        trailing_pe=values["trailing_pe"],
        trailing_ps=values["trailing_ps"],
    )
