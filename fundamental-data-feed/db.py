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
from narratives import ChunkRow, SectionRow

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

_UPSERT_SECTION_SQL = """
INSERT INTO filing_sections (
    cik, accession, form_type, filed_date, period_end,
    section, content, char_count
)
VALUES (
    %(cik)s, %(accession)s, %(form_type)s, %(filed_date)s, %(period_end)s,
    %(section)s, %(content)s, %(char_count)s
)
ON CONFLICT ON CONSTRAINT filing_sections_natural_key DO UPDATE SET
    content = EXCLUDED.content,
    char_count = EXCLUDED.char_count
RETURNING id
"""

_UPSERT_CHUNK_SQL = """
INSERT INTO filing_chunks (section_id, chunk_index, content, token_count)
VALUES (%(section_id)s, %(chunk_index)s, %(content)s, %(token_count)s)
ON CONFLICT ON CONSTRAINT filing_chunks_section_index_key DO UPDATE SET
    content = EXCLUDED.content,
    token_count = EXCLUDED.token_count,
    embedding = CASE
        WHEN filing_chunks.content = EXCLUDED.content THEN filing_chunks.embedding
        ELSE NULL
    END
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


def upsert_sections(conn: Connection, rows: Sequence[SectionRow]) -> dict[tuple[str, str], int]:
    """Insert or refresh filing sections, returning (accession, section) -> id.

    Re-parsing a filing can change the text, so an existing row is updated rather
    than skipped; the chunks hanging off it are rebuilt by `replace_chunks`.
    """
    ids: dict[tuple[str, str], int] = {}
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(_UPSERT_SECTION_SQL, row)
            ids[(row["accession"], row["section"])] = cur.fetchone()["id"]
    return ids


def replace_chunks(conn: Connection, section_id: int, rows: Sequence[ChunkRow]) -> int:
    """Rewrite one section's chunks, returning how many are stored.

    Upserting by index instead of deleting and reinserting keeps embeddings
    across cycles; a chunk whose text changed loses its vector, since a stale
    embedding would retrieve the wrong passage.
    """
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(_UPSERT_CHUNK_SQL, rows)
        cur.execute(
            "DELETE FROM filing_chunks WHERE section_id = %s AND chunk_index >= %s",
            (section_id, len(rows)),
        )
    return len(rows)
