"""Section text cleanup. Needs the feed environment for edgartools.

Run from the feed directory: uv run pytest
"""

from sources import narratives


def test_clean_collapses_table_padding_and_blank_line_runs():
    dirty = "Item 7.\u00a0\u00a0 MD&A\n\n\n\nRevenue      1,234"
    assert narratives._clean(dirty) == "Item 7. MD&A\n\nRevenue 1,234"
