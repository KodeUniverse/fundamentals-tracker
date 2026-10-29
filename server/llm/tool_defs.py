"""Tool schemas and result types.

Kept apart from `tools.py` so the agent loop and its tests do not need a
database connection or an HTTP client just to know what a tool looks like.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SECTIONS = ["risk_factors", "mda"]

# How much filing text one tool call may put in front of the model.
MAX_SEARCH_HITS = 4
MAX_NEW_PARAGRAPHS = 8
PARAGRAPH_PREVIEW_CHARS = 900


class ToolError(ValueError):
    """A call the model can fix by trying again, e.g. an unknown ticker."""


@dataclass
class ToolResult:
    content: dict[str, Any]
    citations: list[dict[str, Any]] = field(default_factory=list)


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_fundamentals",
            "description": (
                "Reported annual figures for one company: revenue, net income, diluted EPS, "
                "gross profit, operating income, margins, and year-over-year growth. "
                "Use for any question about reported numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker of a tracked company"},
                    "years": {
                        "type": "integer",
                        "description": "How many fiscal years back to return, newest first. Default 3.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_fundamentals",
            "description": (
                "Rank all tracked companies by one metric, highest first. Use for comparative "
                "questions such as 'which company had the highest gross margin'. The ranking is "
                "computed, so report it as returned instead of deriving your own."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": [
                            "revenue",
                            "net_income",
                            "eps_diluted",
                            "gross_margin",
                            "operating_margin",
                            "revenue_yoy",
                        ],
                    },
                    "fiscal_year": {
                        "type": "integer",
                        "description": "Fiscal year to compare; omit for each company's latest reported year.",
                    },
                },
                "required": ["metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_valuation",
            "description": (
                "Trailing P/E and P/S for one company, with the price date, fiscal period end, "
                "share count, and market cap. Use for valuation questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker of a tracked company"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_filings",
            "description": (
                "Full-text search over 10-K narrative sections (risk factors and MD&A). "
                "Returns ranked passages with ticker, section, fiscal period, and filing "
                "accession. Use for thematic questions, or when you need quotes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords, quoted phrase, or words joined by OR.",
                    },
                    "ticker": {"type": "string", "description": "Restrict to one company; omit for all five."},
                    "section": {"type": "string", "enum": SECTIONS},
                    "fiscal_year": {"type": "integer", "description": "Fiscal year of the period end."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diff_filing_section",
            "description": (
                "Paragraphs present in the latest 10-K section but not in the prior year's. "
                "Use for 'what new risk factors did they add'. The comparison is computed; "
                "do not attempt to diff sections yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Ticker of a tracked company"},
                    "section": {"type": "string", "enum": SECTIONS},
                },
                "required": ["ticker", "section"],
            },
        },
    },
]
