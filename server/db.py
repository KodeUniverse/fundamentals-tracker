"""Database access for the API.

All SQL lives here. Routes never build queries, and the connection pool is
opened and closed by the app lifespan in main.py.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    min_size=1,
    max_size=4,
    kwargs={"row_factory": dict_row},
)

_FACT_COLUMNS = """
    c.taxonomy,
    c.name AS concept,
    f.unit,
    f.value::float8 AS value,
    f.start_date,
    f.end_date,
    f.form_type,
    f.filed_date,
    f.accession,
    f.filing_fy,
    f.filing_fp
"""


def list_entities() -> list[dict[str, Any]]:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.cik, e.ticker, e.entity_name, count(f.id) AS fact_count
            FROM entities e
            LEFT JOIN facts f ON f.cik = e.cik
            GROUP BY e.cik, e.ticker, e.entity_name
            ORDER BY e.ticker
            """
        )
        return cur.fetchall()


def get_entity(ticker: str) -> dict[str, Any] | None:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.cik, e.ticker, e.entity_name, count(f.id) AS fact_count
            FROM entities e
            LEFT JOIN facts f ON f.cik = e.cik
            WHERE e.ticker = %s
            GROUP BY e.cik, e.ticker, e.entity_name
            LIMIT 1
            """,
            (ticker,),
        )
        return cur.fetchone()


def list_concepts(cik: int) -> list[dict[str, Any]]:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.taxonomy,
                   c.name,
                   c.label,
                   count(*)         AS fact_count,
                   min(f.end_date)   AS first_period,
                   max(f.end_date)   AS last_period
            FROM facts f
            JOIN concepts c ON c.id = f.concept_id
            WHERE f.cik = %s
            GROUP BY c.taxonomy, c.name, c.label
            ORDER BY c.name
            """,
            (cik,),
        )
        return cur.fetchall()


def list_facts(
    cik: int,
    concept: str | None = None,
    taxonomy: str | None = None,
    form_type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Raw facts, newest period first. Filters are optional."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {_FACT_COLUMNS}
            FROM facts f
            JOIN concepts c ON c.id = f.concept_id
            WHERE f.cik = %(cik)s
              AND (%(concept)s::text IS NULL OR c.name = %(concept)s)
              AND (%(taxonomy)s::text IS NULL OR c.taxonomy = %(taxonomy)s)
              AND (%(form_type)s::text IS NULL OR f.form_type = %(form_type)s)
              AND (%(start_date)s::date IS NULL OR f.end_date >= %(start_date)s)
              AND (%(end_date)s::date IS NULL OR f.end_date <= %(end_date)s)
            ORDER BY f.end_date DESC, c.name
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {
                "cik": cik,
                "concept": concept,
                "taxonomy": taxonomy,
                "form_type": form_type,
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit,
                "offset": offset,
            },
        )
        return cur.fetchall()


def annual_metric_facts(cik: int, names: Sequence[str]) -> list[dict[str, Any]]:
    """Annual (duration ~1 year) us-gaap facts for the given concept names.

    Keeps the most recently filed value per concept and period, so restatements
    win over the original filing.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (c.name, f.end_date)
                   c.name, f.end_date, f.value::float8 AS value, f.unit
            FROM facts f
            JOIN concepts c ON c.id = f.concept_id
            WHERE f.cik = %(cik)s
              AND c.taxonomy = 'us-gaap'
              AND c.name = ANY(%(names)s)
              AND f.start_date IS NOT NULL
              AND f.end_date - f.start_date BETWEEN 300 AND 400
            ORDER BY c.name, f.end_date, f.filed_date DESC
            """,
            {"cik": cik, "names": list(names)},
        )
        return cur.fetchall()


def list_filings(cik: int) -> list[dict[str, Any]]:
    """Stored narrative sections for one company, newest period first."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.accession,
                   s.form_type,
                   s.filed_date,
                   s.period_end,
                   s.section,
                   s.char_count,
                   count(c.id) AS chunk_count
            FROM filing_sections s
            LEFT JOIN filing_chunks c ON c.section_id = s.id
            WHERE s.cik = %s
            GROUP BY s.id
            ORDER BY s.period_end DESC, s.section
            """,
            (cik,),
        )
        return cur.fetchall()


def get_filing_section(cik: int, section: str, year: int | None = None) -> dict[str, Any] | None:
    """One stored section, whole. `year` matches the fiscal period end."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT accession, form_type, filed_date, period_end, section, content, char_count
            FROM filing_sections
            WHERE cik = %(cik)s
              AND section = %(section)s
              AND (%(year)s::int IS NULL OR date_part('year', period_end) = %(year)s)
            ORDER BY period_end DESC
            LIMIT 1
            """,
            {"cik": cik, "section": section, "year": year},
        )
        return cur.fetchone()


def search_chunks(
    query: str,
    cik: int | None = None,
    section: str | None = None,
    year: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Ranked narrative chunks for a full-text query, best match first.

    Uses Postgres `websearch_to_tsquery`, so the caller can write plain words,
    "quoted phrases", or OR. Missing embeddings are not a problem: this path
    needs no model.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH q AS (SELECT websearch_to_tsquery('english', %(query)s) AS ts)
            SELECT e.ticker,
                   s.section,
                   s.period_end,
                   s.accession,
                   c.chunk_index,
                   ts_rank(c.search, q.ts)::float8 AS rank,
                   c.content
            FROM filing_chunks c
            JOIN filing_sections s ON s.id = c.section_id
            JOIN entities e ON e.cik = s.cik
            CROSS JOIN q
            WHERE c.search @@ q.ts
              AND (%(cik)s::bigint IS NULL OR s.cik = %(cik)s)
              AND (%(section)s::text IS NULL OR s.section = %(section)s)
              AND (%(year)s::int IS NULL OR date_part('year', s.period_end) = %(year)s)
            ORDER BY rank DESC, s.period_end DESC
            LIMIT %(limit)s
            """,
            {
                "query": query,
                "cik": cik,
                "section": section,
                "year": year,
                "limit": limit,
            },
        )
        return cur.fetchall()


def list_prices(
    cik: int,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Daily bars for one company, newest first."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT trade_date,
                   close::float8     AS close,
                   adj_close::float8 AS adj_close,
                   volume
            FROM prices
            WHERE cik = %(cik)s
              AND (%(start_date)s::date IS NULL OR trade_date >= %(start_date)s)
              AND (%(end_date)s::date IS NULL OR trade_date <= %(end_date)s)
            ORDER BY trade_date DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {
                "cik": cik,
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit,
                "offset": offset,
            },
        )
        return cur.fetchall()


def latest_price(cik: int) -> dict[str, Any] | None:
    """Most recent stored bar, or None when the company has no prices yet."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT trade_date, close::float8 AS close
            FROM prices
            WHERE cik = %s
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (cik,),
        )
        return cur.fetchone()


def share_facts(cik: int, names: Sequence[str]) -> list[dict[str, Any]]:
    """Instant share-count facts for the given tags, newest period first.

    Several tags are queried because coverage differs: Alphabet has no dei
    cover-page count (it reports shares per class, which this pipeline drops as
    dimensioned), so the us-gaap total is the only one available.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.name, f.end_date, f.filed_date, f.value::float8 AS value
            FROM facts f
            JOIN concepts c ON c.id = f.concept_id
            WHERE f.cik = %(cik)s
              AND c.name = ANY(%(names)s)
              AND f.start_date IS NULL
            ORDER BY f.end_date DESC, f.filed_date DESC
            """,
            {"cik": cik, "names": list(names)},
        )
        return cur.fetchall()
