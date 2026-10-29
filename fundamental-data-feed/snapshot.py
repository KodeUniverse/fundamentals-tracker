"""Seed an empty database from the committed dump.

Seeds the db from the dumped snapshot, then carries on with live ingestion, which
upserts over the top. Loading only happens when the database is empty, so it can
never overwrite newer data.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

from psycopg import Connection

SNAPSHOT_FILE = "tracker.sql.gz"


def snapshot_path() -> Path | None:
    """The configured snapshot, or None when the feature is off or the file is absent."""
    enabled = os.getenv("USE_SNAPSHOT_IF_NO_NETWORK", "").lower() in {"1", "true", "yes"}
    if not enabled:
        return None

    directory = os.getenv("SNAPSHOT_DIR", "").strip()
    if not directory:
        return None

    path = Path(directory) / SNAPSHOT_FILE
    return path if path.is_file() else None


def database_is_empty(conn: Connection) -> bool:
    """True when no facts have been ingested yet.

    Facts are the indicator because every cycle writes them; the narrative and
    price tables can be empty in a partially loaded database. The check runs in
    its own transaction: an open one would hold a lock on `facts` and block the
    dump's DDL that reloads it.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT NOT EXISTS (SELECT 1 FROM facts) AS empty")
        return bool(cur.fetchone()["empty"])


def load(path: Path, timeout: float = 600.0) -> int:
    """Stream the dump into psql and return its exit code.

    `--single-transaction` keeps a failed load from leaving half a schema behind,
    and the timeout stops a lock wait from hanging the feed forever.
    """
    process = subprocess.Popen(
        ["psql", os.environ["DATABASE_URL"], "-v", "ON_ERROR_STOP=1", "--single-transaction", "-q"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
    )
    try:
        with gzip.open(path, "rb") as dump:
            shutil.copyfileobj(dump, process.stdin)
    except BrokenPipeError:  # psql stopped early; its exit code explains why
        pass
    finally:
        process.stdin.close()

    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        return 1

