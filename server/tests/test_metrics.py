"""Metric, share-count, and valuation math. No database needed.

Run from the server directory: uv run pytest
"""

from datetime import date

import pytest

from analysis import metrics


def fact(name: str, end: str, value: float) -> dict:
    return {"name": name, "end_date": date.fromisoformat(end), "value": value}


def share(name: str, end: str, filed: str, value: float) -> dict:
    return {
        "name": name,
        "end_date": date.fromisoformat(end),
        "filed_date": date.fromisoformat(filed),
        "value": value,
    }


# --- tag resolution ---------------------------------------------------------


def test_series_prefers_the_first_tag_in_the_list():
    rows = [
        fact("Revenues", "2024-12-31", 100.0),
        fact("RevenueFromContractWithCustomerExcludingAssessedTax", "2024-12-31", 110.0),
    ]
    series = metrics.series_by_period(rows, metrics.REVENUE_TAGS)
    assert series[date(2024, 12, 31)] == 110.0


def test_series_falls_through_to_later_tags_for_other_periods():
    rows = [
        fact("SalesRevenueNet", "2015-09-26", 50.0),
        fact("Revenues", "2016-09-24", 60.0),
    ]
    series = metrics.series_by_period(rows, metrics.REVENUE_TAGS)
    assert series[date(2015, 9, 26)] == 50.0
    assert series[date(2016, 9, 24)] == 60.0


def test_series_ignores_unrelated_tags():
    series = metrics.series_by_period([fact("Assets", "2024-12-31", 1.0)], metrics.REVENUE_TAGS)
    assert series == {}


# --- ratios and growth ------------------------------------------------------


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (46.9, 100.0, 0.469),
        (None, 100.0, None),
        (100.0, None, None),
        (100.0, 0, None),
    ],
)
def test_ratio(numerator, denominator, expected):
    assert metrics.ratio(numerator, denominator) == expected


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        (110.0, 100.0, 0.1),
        (None, 100.0, None),
        (110.0, None, None),
        (110.0, 0, None),
    ],
)
def test_growth(current, previous, expected):
    assert metrics.growth(current, previous) == expected


# --- share count ------------------------------------------------------------


def test_latest_shares_prefers_the_most_recent_period():
    rows = [
        share("CommonStockSharesOutstanding", "2025-12-31", "2026-02-01", 100.0),
        share("CommonStockSharesOutstanding", "2026-06-30", "2026-08-01", 110.0),
    ]
    assert metrics.latest_shares(rows) == (110.0, date(2026, 6, 30))


def test_latest_shares_lets_the_cover_page_tag_win_a_tie():
    rows = [
        share("CommonStockSharesOutstanding", "2026-06-30", "2026-08-01", 111.0),
        share("EntityCommonStockSharesOutstanding", "2026-06-30", "2026-08-01", 110.0),
    ]
    assert metrics.latest_shares(rows)[0] == 110.0


def test_latest_shares_is_none_without_a_share_tag():
    assert metrics.latest_shares([]) is None
    assert metrics.latest_shares([share("Assets", "2026-06-30", "2026-08-01", 1.0)]) is None


# --- valuation --------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "eps", "expected"),
    [
        (300.0, 10.0, 30.0),
        (300.0, 0, None),
        (300.0, -2.5, None),
        (None, 10.0, None),
        (300.0, None, None),
    ],
)
def test_trailing_pe(price, eps, expected):
    assert metrics.trailing_pe(price, eps) == expected


@pytest.mark.parametrize(
    ("market_cap", "revenue", "expected"),
    [
        (1000.0, 250.0, 4.0),
        (None, 250.0, None),
        (1000.0, 0, None),
    ],
)
def test_trailing_ps(market_cap, revenue, expected):
    assert metrics.trailing_ps(market_cap, revenue) == expected


def test_market_cap_is_price_times_shares():
    values = metrics.valuation(price=100.0, shares=2.0e9, eps=5.0, revenue=8.0e10)
    assert values["market_cap"] == 2.0e11
    assert values["trailing_pe"] == 20.0
    assert values["trailing_ps"] == 2.5


def test_missing_shares_only_breaks_the_sales_multiple():
    values = metrics.valuation(price=100.0, shares=None, eps=5.0, revenue=200.0)
    assert values["market_cap"] is None
    assert values["trailing_ps"] is None
    assert values["trailing_pe"] == 20.0


# --- annual assembly --------------------------------------------------------


def test_gross_profit_falls_back_to_revenue_minus_cost():
    rows = [
        fact("Revenues", "2024-12-31", 100.0),
        fact("CostOfRevenue", "2024-12-31", 40.0),
    ]
    annual = metrics.annual_metrics(rows)
    assert annual[0]["gross_profit"] == 60.0
    assert annual[0]["gross_margin"] == 0.6


def test_reported_gross_profit_wins_over_the_calculation():
    rows = [
        fact("Revenues", "2024-12-31", 100.0),
        fact("CostOfRevenue", "2024-12-31", 40.0),
        fact("GrossProfit", "2024-12-31", 55.0),
    ]
    assert metrics.annual_metrics(rows)[0]["gross_profit"] == 55.0


def test_rows_are_newest_first_with_growth_from_the_prior_year():
    rows = [
        fact("Revenues", "2022-12-31", 100.0),
        fact("Revenues", "2023-12-31", 110.0),
        fact("Revenues", "2024-12-31", 121.0),
    ]
    annual = metrics.annual_metrics(rows)

    assert [row["fiscal_year"] for row in annual] == [2024, 2023, 2022]
    assert annual[0]["revenue_yoy"] == 0.1
    assert annual[1]["revenue_yoy"] == 0.1
    assert annual[2]["revenue_yoy"] is None


def test_slicing_keeps_growth_for_the_oldest_returned_year():
    rows = [fact("Revenues", f"{year}-12-31", 100.0 + year) for year in range(2020, 2025)]
    annual = metrics.annual_metrics(rows, years=2)

    assert len(annual) == 2
    assert annual[-1]["revenue_yoy"] is not None


def test_periods_without_revenue_are_skipped():
    rows = [
        fact("Revenues", "2023-12-31", 100.0),
        fact("NetIncomeLoss", "2024-12-31", 10.0),
    ]
    assert [row["fiscal_year"] for row in metrics.annual_metrics(rows)] == [2023]


def test_missing_tags_come_back_as_none():
    row = metrics.annual_metrics([fact("Revenues", "2024-12-31", 100.0)])[0]

    assert row["net_income"] is None
    assert row["eps_diluted"] is None
    assert row["operating_margin"] is None
