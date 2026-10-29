"""yfinance frame conversion. No network, no database.

Run from the feed directory: uv run pytest
"""

from datetime import date

import pandas as pd

from sources import prices


def frame(closes, volumes, adj_closes=None):
    """A frame shaped like yfinance's, with a timezone-aware index."""
    return pd.DataFrame(
        {
            "Close": closes,
            "Adj Close": adj_closes if adj_closes is not None else closes,
            "Volume": volumes,
        },
        index=pd.to_datetime(["2026-10-26", "2026-10-27"]).tz_localize("America/New_York"),
    )


def test_converts_bars_and_keeps_the_exchange_date():
    rows = prices.price_rows(frame([100.0, 102.0], [1000, 1200], [99.0, 101.0]), cik=7)

    assert [row["trade_date"] for row in rows] == [date(2026, 10, 26), date(2026, 10, 27)]
    assert rows[1]["close"] == 102.0
    assert rows[0]["adj_close"] == 99.0
    assert rows[0]["volume"] == 1000
    assert rows[0]["cik"] == 7


def test_skips_a_bar_with_no_close():
    rows = prices.price_rows(frame([100.0, float("nan")], [1000, 0]), cik=7)

    assert len(rows) == 1
    assert rows[0]["trade_date"] == date(2026, 10, 26)


def test_empty_frame_gives_no_rows():
    assert prices.price_rows(pd.DataFrame(columns=["Close", "Adj Close", "Volume"]), cik=7) == []
