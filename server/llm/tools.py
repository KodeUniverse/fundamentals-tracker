"""Tool implementations the model can call.

Every tool returns data that is already computed: margins, growth, multiples,
rankings, and year-over-year paragraph differences are done in SQL or Python.
The model formats and explains; it never does arithmetic.

The schemas the model sees live in `tool_defs.py`, so the agent loop can be
tested without a database.
"""

from __future__ import annotations

from typing import Any, Callable

import db
from analysis import metrics, narrative, valuation
from llm.tool_defs import (
    MAX_NEW_PARAGRAPHS,
    MAX_SEARCH_HITS,
    PARAGRAPH_PREVIEW_CHARS,
    ToolError,
    ToolResult,
)

_METRIC_KEYS = {
    "revenue": "revenue",
    "net_income": "net_income",
    "eps_diluted": "eps_diluted",
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "revenue_yoy": "revenue_yoy",
}


def get_fundamentals(ticker: str, years: int = 3) -> ToolResult:
    entity = _entity(ticker)
    rows = db.annual_metric_facts(entity["cik"], metrics.ALL_TAGS)
    annual = metrics.annual_metrics(rows, years)

    return ToolResult(
        content={
            "ticker": entity["ticker"],
            "entity_name": entity["entity_name"],
            "annual": [_jsonable(row) for row in annual],
        },
        citations=[_citation(entity["ticker"], row["period_end"]) for row in annual],
    )


def compare_fundamentals(metric: str, fiscal_year: int | None = None) -> ToolResult:
    key = _metric_key(metric)
    ranking: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []

    for entity in db.list_entities():
        rows = db.annual_metric_facts(entity["cik"], metrics.ALL_TAGS)
        row = _pick_year(metrics.annual_metrics(rows, years=10), fiscal_year)
        if row is None or row[key] is None:
            continue
        ranking.append(
            {
                "ticker": entity["ticker"],
                "value": row[key],
                "fiscal_year": row["fiscal_year"],
                "period_end": row["period_end"].isoformat(),
            }
        )
        citations.append(_citation(entity["ticker"], row["period_end"]))

    ranking.sort(key=lambda item: item["value"], reverse=True)
    return ToolResult(
        content={"metric": metric, "fiscal_year": fiscal_year, "ranking": ranking},
        citations=citations,
    )


def get_valuation(ticker: str) -> ToolResult:
    entity = _entity(ticker)
    payload = valuation.compute_valuation(entity["cik"])
    if payload is None:
        raise ToolError(f"no price or annual fundamentals stored for {entity['ticker']}")

    return ToolResult(
        content={"ticker": entity["ticker"], **_jsonable(payload)},
        citations=[_citation(entity["ticker"], payload["period_end"])],
    )


def search_filings(
    query: str,
    ticker: str | None = None,
    section: str | None = None,
    fiscal_year: int | None = None,
) -> ToolResult:
    cik = _entity(ticker)["cik"] if ticker else None
    hits = db.search_chunks(
        query=query,
        cik=cik,
        section=section,
        year=fiscal_year,
        limit=MAX_SEARCH_HITS,
    )

    passages = [
        {
            "ticker": hit["ticker"],
            "section": hit["section"],
            "period_end": hit["period_end"].isoformat(),
            "accession": hit["accession"],
            "chunk_index": hit["chunk_index"],
            "text": hit["content"],
        }
        for hit in hits
    ]
    return ToolResult(
        content={"query": query, "hit_count": len(passages), "passages": passages},
        citations=[
            _citation(hit["ticker"], hit["period_end"], hit["section"], hit["accession"], hit["chunk_index"])
            for hit in hits
        ],
    )


def diff_filing_section(ticker: str, section: str) -> ToolResult:
    entity = _entity(ticker)
    stored = db.list_sections(entity["cik"], section, limit=2)
    if len(stored) < 2:
        raise ToolError(
            f"only {len(stored)} stored {section} section(s) for {entity['ticker']}; need two to compare"
        )

    latest, prior = stored[0], stored[1]
    added = narrative.new_paragraphs(latest["content"], prior["content"])
    shown = added[:MAX_NEW_PARAGRAPHS]

    return ToolResult(
        content={
            "ticker": entity["ticker"],
            "section": section,
            "latest_period_end": latest["period_end"].isoformat(),
            "prior_period_end": prior["period_end"].isoformat(),
            "new_paragraph_count": len(added),
            "truncated": len(added) > len(shown),
            "new_paragraphs": [
                {"text": paragraph[:PARAGRAPH_PREVIEW_CHARS], "chars": len(paragraph)} for paragraph in shown
            ],
        },
        citations=[
            _citation(entity["ticker"], latest["period_end"], section, latest["accession"]),
            _citation(entity["ticker"], prior["period_end"], section, prior["accession"]),
        ],
    )


TOOL_FUNCTIONS: dict[str, Callable[..., ToolResult]] = {
    "get_fundamentals": get_fundamentals,
    "compare_fundamentals": compare_fundamentals,
    "get_valuation": get_valuation,
    "search_filings": search_filings,
    "diff_filing_section": diff_filing_section,
}


def _entity(ticker: str) -> dict[str, Any]:
    entity = db.get_entity(str(ticker).upper())
    if entity is None:
        tracked = ", ".join(sorted(entity["ticker"] or "" for entity in db.list_entities()))
        raise ToolError(f"unknown ticker {ticker}; tracked companies are {tracked}")
    return entity


def _metric_key(metric: str) -> str:
    key = _METRIC_KEYS.get(metric)
    if key is None:
        raise ToolError(f"unknown metric {metric}; use one of {', '.join(_METRIC_KEYS)}")
    return key


def _pick_year(annual: list[dict[str, Any]], fiscal_year: int | None) -> dict[str, Any] | None:
    if fiscal_year is None:
        return annual[0] if annual else None
    return next((row for row in annual if row["fiscal_year"] == fiscal_year), None)


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    """Dates to ISO strings so the tool content can be serialized as JSON."""
    return {key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in payload.items()}


def _citation(
    ticker: str | None,
    period_end: Any,
    section: str | None = None,
    accession: str | None = None,
    chunk_index: int | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker or "",
        "section": section,
        "period_end": period_end.isoformat() if hasattr(period_end, "isoformat") else period_end,
        "accession": accession,
        "chunk_index": chunk_index,
    }
