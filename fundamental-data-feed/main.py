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

from db import connect, insert_facts, upsert_concepts, upsert_entity
from facts import build_entity, collect_concepts, get_all_facts, iter_fact_rows

log = logging.getLogger("feed")


def ingest_ticker(conn, ticker: str) -> None:
    """Fetch, normalize and persist one ticker in a single transaction."""
    company = edgar.Company(ticker)
    entity = build_entity(company, ticker)
    facts = get_all_facts(company)
    concepts = collect_concepts(facts)

    with conn.transaction():
        upsert_entity(conn, entity.cik, entity.entity_name, entity.ticker)
        concept_ids = upsert_concepts(conn, concepts)
        rows = list(iter_fact_rows(facts, entity.cik, concept_ids))
        inserted = insert_facts(conn, rows)

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
