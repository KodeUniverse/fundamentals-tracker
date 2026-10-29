"""Fetch daily prices from Yahoo Finance and normalize them to DB rows.

Yahoo back-adjusts its entire history for splits, so a stored window is only
internally consistent if it is rewritten at the current scale. That is why the
feed refetches the full window every cycle instead of appending new days.

As with `facts.py`, the caller constructs the ticker; nothing here touches the
database.
"""

from __future__ import annotations

import warnings
from datetime import date, timedelta
from typing import Any

import pandas as pd
import yfinance as yf
from pandas.errors import Pandas4Warning

from rows import PriceRow

# yfinance 1.7.0 still calls pandas APIs that pandas 3.1 deprecates. Set this
# after importing yfinance: pandas registers its own filters at import time and
# filterwarnings prepends, so a filter installed earlier loses to them.
warnings.filterwarnings("ignore", category=Pandas4Warning)

# Long enough to span both recent splits (AAPL 2020, NVDA 2024), which is what
# makes the adjusted series visibly continuous rather than a cliff.
HISTORY_PERIOD_YRS = 10


def collect_prices(cik: int, ticker: str) -> list[PriceRow]:
    """Daily bars for the configured window, oldest first.

    Returns an empty list when Yahoo has nothing for the ticker, which the caller
    treats the same as a failed fetch: keep whatever is already stored.
    """
    start = date.today() - timedelta(days=365 * HISTORY_PERIOD_YRS)
    frame = yf.Ticker(ticker).history(start=start, auto_adjust=False)
    return price_rows(frame, cik)


def price_rows(frame: Any, cik: int) -> list[PriceRow]:
    """Convert a yfinance OHLCV frame to rows, skipping bars without a close.

    The frame index is timezone-aware, so `.date()` gives the exchange's trading
    date rather than a UTC date that can be off by one.
    """
    rows: list[PriceRow] = []

    for timestamp, bar in frame.iterrows():
        close = bar["Close"]
        if pd.isna(close):
            continue
        rows.append(
            PriceRow(
                cik=cik,
                trade_date=timestamp.date(),
                close=float(close),
                adj_close=_optional_float(bar.get("Adj Close")),
                volume=_optional_int(bar.get("Volume")),
            )
        )

    return rows


def _optional_float(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None or pd.isna(value) else int(value)
