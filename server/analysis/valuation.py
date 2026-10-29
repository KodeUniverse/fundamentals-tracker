"""Cross-source valuation, shared by the HTTP route and the agent tools.

One function so the number the model sees and the number `/valuation` returns
cannot drift apart.
"""

from __future__ import annotations

from typing import Any

import db
from analysis import metrics


def compute_valuation(cik: int) -> dict[str, Any] | None:
    """Latest price joined with the latest annual fundamentals.

    Returns None when the company has no stored price or no annual metrics;
    callers turn that into a 404 or a tool error rather than a made-up number.
    """
    price = db.latest_price(cik)
    if price is None:
        return None

    annual = metrics.annual_metrics(db.annual_metric_facts(cik, metrics.ALL_TAGS), years=1)
    if not annual:
        return None

    latest = annual[0]
    shares = metrics.latest_shares(db.share_facts(cik, metrics.SHARES_TAGS))
    values = metrics.valuation(
        price=price["close"],
        shares=shares[0] if shares else None,
        eps=latest["eps_diluted"],
        revenue=latest["revenue"],
    )

    return {
        "price_date": price["trade_date"],
        "price": price["close"],
        "fiscal_year": latest["fiscal_year"],
        "period_end": latest["period_end"],
        "eps_diluted": latest["eps_diluted"],
        "revenue": latest["revenue"],
        "net_income": latest["net_income"],
        "shares_outstanding": shares[0] if shares else None,
        "shares_as_of": shares[1] if shares else None,
        "market_cap": values["market_cap"],
        "trailing_pe": values["trailing_pe"],
        "trailing_ps": values["trailing_ps"],
    }
