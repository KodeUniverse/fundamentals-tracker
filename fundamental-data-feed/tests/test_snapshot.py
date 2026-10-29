"""Snapshot discovery. No database and no filesystem beyond a temp directory.

Run from the feed directory: uv run pytest
"""

import pytest

from snapshot import snapshot_path


def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SEED_FROM_SNAPSHOT", raising=False)
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    (tmp_path / "tracker.sql.gz").write_bytes(b"")

    assert snapshot_path() is None


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_disabled_by_any_off_value(value, tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_FROM_SNAPSHOT", value)
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    (tmp_path / "tracker.sql.gz").write_bytes(b"")

    assert snapshot_path() is None


def test_missing_directory_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_FROM_SNAPSHOT", "true")
    monkeypatch.delenv("SNAPSHOT_DIR", raising=False)

    assert snapshot_path() is None


def test_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_FROM_SNAPSHOT", "true")
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))

    assert snapshot_path() is None


def test_returns_the_dump_when_enabled_and_present(tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_FROM_SNAPSHOT", "true")
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    dump = tmp_path / "tracker.sql.gz"
    dump.write_bytes(b"")

    assert snapshot_path() == dump
