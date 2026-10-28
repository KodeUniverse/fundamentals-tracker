"""Tests for the yfinance frame conversion. No network, no database.

Run from the feed directory: .venv/bin/python -m unittest discover -v
"""

import unittest
from datetime import date

import pandas as pd

import prices


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


class PriceRowsTest(unittest.TestCase):
    def test_converts_bars_and_keeps_the_exchange_date(self):
        rows = prices.price_rows(frame([100.0, 102.0], [1000, 1200], [99.0, 101.0]), cik=7)
        self.assertEqual([row["trade_date"] for row in rows], [date(2026, 10, 26), date(2026, 10, 27)])
        self.assertEqual(rows[1]["close"], 102.0)
        self.assertEqual(rows[0]["adj_close"], 99.0)
        self.assertEqual(rows[0]["volume"], 1000)
        self.assertEqual(rows[0]["cik"], 7)

    def test_skips_a_bar_with_no_close(self):
        rows = prices.price_rows(frame([100.0, float("nan")], [1000, 0]), cik=7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_date"], date(2026, 10, 26))

    def test_empty_frame_gives_no_rows(self):
        rows = prices.price_rows(pd.DataFrame(columns=["Close", "Adj Close", "Volume"]), cik=7)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
