"""Row shapes shared by the source adapters and the persistence layer.

`TypedDict`s because psycopg named parameters need mappings. Keeping them here
means `db.py` can describe what it stores without importing the fetchers.
"""

from __future__ import annotations

from datetime import date
from typing import TypedDict


class ConceptRow(TypedDict):
    taxonomy: str
    name: str
    label: str | None


class FactRow(TypedDict):
    cik: int
    concept_id: int
    unit: str
    value: float
    start_date: date | None
    end_date: date
    form_type: str
    filed_date: date
    accession: str
    filing_fy: int | None
    filing_fp: str | None


class SectionRow(TypedDict):
    cik: int
    accession: str
    form_type: str
    filed_date: date
    period_end: date
    section: str
    content: str
    char_count: int


class ChunkRow(TypedDict):
    section_id: int
    chunk_index: int
    content: str
    token_count: int


class PriceRow(TypedDict):
    cik: int
    trade_date: date
    close: float
    adj_close: float | None
    volume: int | None
