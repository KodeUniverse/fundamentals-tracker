"""Persistence layer for normalized EDGAR facts.

Each function runs inside a transaction owned by the caller, so one ticker's
entity/concepts/facts land atomically.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from facts import ConceptRow, FactRow

_INSERT_FACTS_SQL = """
INSERT INTO facts (
    cik, concept_id, unit, value,
    start_date, end_date,
    form_type, filed_date, accession,
    filing_fy, filing_fp
)
VALUES (
    %(cik)s, %(concept_id)s, %(unit)s, %(value)s,
    %(start_date)s, %(end_date)s,
    %(form_type)s, %(filed_date)s, %(accession)s,
    %(filing_fy)s, %(filing_fp)s
)
ON CONFLICT ON CONSTRAINT facts_natural_key DO NOTHING
"""


def connect() -> Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def upsert_entity(conn: Connection, cik: int, entity_name: str, ticker: str | None) -> None:
    """Insert or refresh one tracked registrant.

    `ticker` is display-only and not unique (GOOGL and GOOG share a CIK), so
    keep the existing value when no ticker is supplied.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entities (cik, entity_name, ticker)
            VALUES (%s, %s, %s)
            ON CONFLICT (cik) DO UPDATE SET
                entity_name = EXCLUDED.entity_name,
                ticker = COALESCE(EXCLUDED.ticker, entities.ticker)
            """,
            (cik, entity_name, ticker),
        )


def upsert_concepts(conn: Connection, concepts: Sequence[ConceptRow]) -> dict[str, int]:
    """Insert any new concepts, then return the `taxonomy:name` -> id map.

    Concepts are small, globally shared reference data, so the whole table is
    read back rather than tracking which ids were just created.
    """
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO concepts (taxonomy, name, label)
            VALUES (%(taxonomy)s, %(name)s, %(label)s)
            ON CONFLICT ON CONSTRAINT concepts_taxonomy_name_key DO NOTHING
            """,
            concepts,
        )
        cur.execute("SELECT id, taxonomy, name FROM concepts")
        return {f"{row['taxonomy']}:{row['name']}": row["id"] for row in cur.fetchall()}


def insert_facts(conn: Connection, rows: Sequence[FactRow]) -> int:
    """Bulk-insert fact rows, returning how many were new.

    The natural-key constraint makes re-ingestion idempotent, which is what lets
    the feed poll on an interval without duplicating history.
    """
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(_INSERT_FACTS_SQL, rows)
        return cur.rowcount
