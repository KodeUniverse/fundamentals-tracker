"""Derived metrics computed from normalized facts.

Values are in the units EDGAR reports (USD for money, USD/shares for EPS).
Margins and growth are fractions, not percentages: 0.469 means 46.9%.

Tag lists are ordered by preference. EDGAR tag usage drifts over time and between
companies, so the first tag present for a period wins.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
GROSS_PROFIT_TAGS = ("GrossProfit",)
COST_OF_REVENUE_TAGS = ("CostOfRevenue", "CostOfGoodsAndServicesSold")
OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
NET_INCOME_TAGS = ("NetIncomeLoss", "ProfitLoss")
EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")

# Ordered by how current the count is, not by taxonomy. The dei cover-page tag is
# the freshest but not every issuer reports it, so the us-gaap balance-sheet total
# is the fallback and the weighted diluted count is the last resort.
SHARES_TAGS = (
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstanding",
)

ALL_TAGS: tuple[str, ...] = (
    REVENUE_TAGS
    + GROSS_PROFIT_TAGS
    + COST_OF_REVENUE_TAGS
    + OPERATING_INCOME_TAGS
    + NET_INCOME_TAGS
    + EPS_TAGS
)


def series_by_period(rows: Iterable[dict[str, Any]], tags: Sequence[str]) -> dict[date, float]:
    """Map period end -> value, preferring tags that appear earlier in `tags`."""
    rank = {tag: i for i, tag in enumerate(tags)}
    best: dict[date, tuple[int, float]] = {}

    for row in rows:
        position = rank.get(row["name"])
        if position is None:
            continue
        current = best.get(row["end_date"])
        if current is None or position < current[0]:
            best[row["end_date"]] = (position, row["value"])

    return {period: value for period, (_, value) in best.items()}


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, 4)


def growth(current: float | None, previous: float | None) -> float | None:
    if current is None or not previous:
        return None
    return round((current - previous) / previous, 4)


def latest_shares(rows: Sequence[dict[str, Any]]) -> tuple[float, date] | None:
    """Freshest share count and the date it was reported for.

    The newest period wins; tag order only breaks ties on the same period, so a
    stale cover-page count never displaces a newer balance-sheet count.
    """
    rank = {tag: index for index, tag in enumerate(SHARES_TAGS)}
    candidates = [row for row in rows if row["name"] in rank]
    if not candidates:
        return None

    best = min(
        candidates,
        key=lambda row: (
            -row["end_date"].toordinal(),
            rank[row["name"]],
            -row["filed_date"].toordinal(),
        ),
    )
    return best["value"], best["end_date"]


def trailing_pe(price: float | None, eps: float | None) -> float | None:
    """Price divided by the latest annual diluted EPS.

    None when EPS is zero or negative: a negative multiple is not a valuation
    signal, so the honest answer is that the ratio does not apply.
    """
    if price is None or eps is None or eps <= 0:
        return None
    return round(price / eps, 2)


def trailing_ps(market_cap: float | None, revenue: float | None) -> float | None:
    if market_cap is None or revenue is None or revenue <= 0:
        return None
    return round(market_cap / revenue, 2)


def valuation(
    price: float,
    shares: float | None,
    eps: float | None,
    revenue: float | None,
) -> dict[str, float | None]:
    """Market cap and the two trailing multiples from one price and one year.

    P/E needs no share count, so it survives a missing shares tag; only P/S
    degrades to null in that case.
    """
    market_cap = None if shares is None else price * shares
    return {
        "market_cap": market_cap,
        "trailing_pe": trailing_pe(price, eps),
        "trailing_ps": trailing_ps(market_cap, revenue),
    }


def annual_metrics(rows: Sequence[dict[str, Any]], years: int = 5) -> list[dict[str, Any]]:
    """One row per fiscal year, newest first.

    Years are anchored on revenue: a period with no revenue figure is skipped.
    YoY is computed before slicing so the oldest returned year still has a
    comparison, as long as EDGAR has one.
    """
    revenue = series_by_period(rows, REVENUE_TAGS)
    gross_profit = series_by_period(rows, GROSS_PROFIT_TAGS)
    cost_of_revenue = series_by_period(rows, COST_OF_REVENUE_TAGS)
    operating_income = series_by_period(rows, OPERATING_INCOME_TAGS)
    net_income = series_by_period(rows, NET_INCOME_TAGS)
    eps = series_by_period(rows, EPS_TAGS)

    annual: list[dict[str, Any]] = []
    previous_revenue: float | None = None
    previous_net_income: float | None = None

    for period in sorted(revenue):
        period_revenue = revenue[period]
        period_gross_profit = gross_profit.get(period)
        if period_gross_profit is None and period_revenue is not None:
            cost = cost_of_revenue.get(period)
            period_gross_profit = None if cost is None else period_revenue - cost

        period_net_income = net_income.get(period)
        period_operating_income = operating_income.get(period)

        annual.append(
            {
                "fiscal_year": period.year,
                "period_end": period,
                "revenue": period_revenue,
                "gross_profit": period_gross_profit,
                "operating_income": period_operating_income,
                "net_income": period_net_income,
                "eps_diluted": eps.get(period),
                "gross_margin": ratio(period_gross_profit, period_revenue),
                "operating_margin": ratio(period_operating_income, period_revenue),
                "revenue_yoy": growth(period_revenue, previous_revenue),
                "net_income_yoy": growth(period_net_income, previous_net_income),
            }
        )

        previous_revenue = period_revenue
        previous_net_income = period_net_income

    return list(reversed(annual[-years:]))
