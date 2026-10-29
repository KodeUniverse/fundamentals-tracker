"""Long-running ingestion feed for the fundamentals tracker.

Each cycle resolves the configured tickers from EDGAR, normalizes every reported
fact, and upserts entities/concepts/facts. Because facts are keyed on
(cik, concept, unit, period, accession) and inserts are `ON CONFLICT DO NOTHING`,
repeated cycles are idempotent: new filings add rows, existing ones are skipped.

Configuration (see .env.example):
  EDGAR_IDENTITY         required by SEC, "Name email@example.com"
  TICKERS                comma-separated, e.g. NVDA,MSFT,AAPL,GOOGL,ETN
  POLL_INTERVAL_SECONDS  seconds between cycles (default 3600)
  RUN_ONCE               set to 1 to ingest once and exit (tests / snapshot runs)
  DATABASE_URL           libpq URL, e.g. postgresql://user:pass@db:5432/tracker
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

import edgar

from db import (
    connect,
    insert_facts,
    replace_chunks,
    upsert_concepts,
    upsert_entity,
    upsert_prices,
    upsert_sections,
)
from sources.facts import build_entity, collect_concepts, fact_rows, get_all_facts
from sources.narratives import chunk_rows, collect_sections
from sources.prices import collect_prices
from snapshot import database_is_empty, load as load_snapshot, snapshot_path

log = logging.getLogger("feed")


def seed_from_snapshot(conn) -> None:
    """Load the committed dump if the database is empty and the snapshot is enabled."""
    path = snapshot_path()
    if path is None:
        return
    if not database_is_empty(conn):
        log.info("database already has facts; not loading %s", path.name)
        return

    log.info("loading snapshot %s", path)
    try:
        code = load_snapshot(path)
    except OSError as exc:  # psql missing from the image, or the dump is unreadable
        log.error("could not load the snapshot: %s", exc)
        return

    if code != 0:
        log.error("snapshot load exited with %s; continuing with live ingestion", code)


def ingest_ticker(conn, ticker: str) -> None:
    """Fetch, normalize and persist one ticker in a single transaction."""
    company = edgar.Company(ticker)
    entity = build_entity(company, ticker)
    facts = get_all_facts(company)
    concepts = collect_concepts(facts)

    try:
        sections = collect_sections(company)
    except Exception:  # narrative parsing is the fragile part; facts still land
        log.exception("%s: narrative extraction failed, ingesting facts only", ticker)
        sections = []

    try:
        prices = collect_prices(entity.cik, ticker)
    except Exception:  # Yahoo is an unofficial scraper; keep the stored window
        log.exception("%s: price fetch failed, keeping stored prices", ticker)
        prices = []

    with conn.transaction():
        upsert_entity(conn, entity.cik, entity.entity_name, entity.ticker)
        concept_ids = upsert_concepts(conn, concepts)
        rows = fact_rows(facts, entity.cik, concept_ids)
        inserted = insert_facts(conn, rows)

        section_ids = upsert_sections(conn, sections)
        chunk_total = 0
        for section in sections:
            section_id = section_ids[(section["accession"], section["section"])]
            chunk_total += replace_chunks(conn, section_id, chunk_rows(section_id, section["content"]))

        price_total = upsert_prices(conn, prices)

    log.info(
        "%s (CIK %s, %s): %d facts, %d concepts, %d new rows (%d already stored)",
        ticker,
        entity.cik,
        entity.entity_name,
        len(facts),
        len(concepts),
        inserted,
        len(rows) - inserted,
    )
    log.info(
        "%s: %d filing sections, %d chunks, %d price bars",
        ticker,
        len(sections),
        chunk_total,
        price_total,
    )


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    identity = os.getenv("EDGAR_IDENTITY", "").strip()
    if not identity:
        log.error("EDGAR_IDENTITY is required; SEC returns 403 without it")
        return 1
    edgar.set_identity(identity)

    tickers = [t.strip().upper() for t in os.getenv("TICKERS", "").split(",") if t.strip()]
    if not tickers:
        log.error("TICKERS is required, e.g. TICKERS=NVDA,MSFT,AAPL,GOOGL,ETN")
        return 1

    interval = int(os.getenv("POLL_INTERVAL_SECONDS", "3600"))
    run_once = os.getenv("RUN_ONCE", "").lower() in {"1", "true", "yes"}

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    conn = connect()
    try:
        seed_from_snapshot(conn)
        while not stop.is_set():
            for ticker in tickers:
                if stop.is_set():
                    break
                try:
                    ingest_ticker(conn, ticker)
                except Exception:  # one bad ticker must not kill the feed
                    log.exception("ingestion failed for %s", ticker)
                    conn.rollback()

            if run_once:
                break
            log.info("cycle complete; sleeping for %ss", interval)
            stop.wait(interval)
    finally:
        conn.close()

    log.info("feed stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
