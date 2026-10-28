"""Tests for the derived metric math. No database needed.

Run from the server directory: python -m unittest discover -v
"""

import unittest
from datetime import date

import metrics


def fact(name: str, end: str, value: float) -> dict:
    return {"name": name, "end_date": date.fromisoformat(end), "value": value}


class SeriesTest(unittest.TestCase):
    def test_prefers_the_first_tag_in_the_list(self):
        rows = [
            fact("Revenues", "2024-12-31", 100.0),
            fact("RevenueFromContractWithCustomerExcludingAssessedTax", "2024-12-31", 110.0),
        ]
        series = metrics.series_by_period(rows, metrics.REVENUE_TAGS)
        self.assertEqual(series[date(2024, 12, 31)], 110.0)

    def test_falls_through_to_later_tags_for_other_periods(self):
        rows = [
            fact("SalesRevenueNet", "2015-09-26", 50.0),
            fact("Revenues", "2016-09-24", 60.0),
        ]
        series = metrics.series_by_period(rows, metrics.REVENUE_TAGS)
        self.assertEqual(series[date(2015, 9, 26)], 50.0)
        self.assertEqual(series[date(2016, 9, 24)], 60.0)

    def test_ignores_unrelated_tags(self):
        series = metrics.series_by_period([fact("Assets", "2024-12-31", 1.0)], metrics.REVENUE_TAGS)
        self.assertEqual(series, {})


class RatioTest(unittest.TestCase):
    def test_ratio_is_a_fraction_rounded_to_four_places(self):
        self.assertEqual(metrics.ratio(46.9, 100.0), 0.469)

    def test_ratio_returns_none_for_missing_or_zero_denominator(self):
        self.assertIsNone(metrics.ratio(None, 100.0))
        self.assertIsNone(metrics.ratio(100.0, None))
        self.assertIsNone(metrics.ratio(100.0, 0))

    def test_growth_handles_missing_and_zero_baseline(self):
        self.assertEqual(metrics.growth(110.0, 100.0), 0.1)
        self.assertIsNone(metrics.growth(None, 100.0))
        self.assertIsNone(metrics.growth(110.0, 0))


class AnnualMetricsTest(unittest.TestCase):
    def test_gross_profit_falls_back_to_revenue_minus_cost(self):
        rows = [
            fact("Revenues", "2024-12-31", 100.0),
            fact("CostOfRevenue", "2024-12-31", 40.0),
        ]
        annual = metrics.annual_metrics(rows)
        self.assertEqual(annual[0]["gross_profit"], 60.0)
        self.assertEqual(annual[0]["gross_margin"], 0.6)

    def test_reported_gross_profit_wins_over_the_calculation(self):
        rows = [
            fact("Revenues", "2024-12-31", 100.0),
            fact("CostOfRevenue", "2024-12-31", 40.0),
            fact("GrossProfit", "2024-12-31", 55.0),
        ]
        self.assertEqual(metrics.annual_metrics(rows)[0]["gross_profit"], 55.0)

    def test_rows_are_newest_first_with_growth_from_the_prior_year(self):
        rows = [
            fact("Revenues", "2022-12-31", 100.0),
            fact("Revenues", "2023-12-31", 110.0),
            fact("Revenues", "2024-12-31", 121.0),
        ]
        annual = metrics.annual_metrics(rows)
        self.assertEqual([row["fiscal_year"] for row in annual], [2024, 2023, 2022])
        self.assertEqual(annual[0]["revenue_yoy"], 0.1)
        self.assertEqual(annual[1]["revenue_yoy"], 0.1)
        self.assertIsNone(annual[2]["revenue_yoy"])

    def test_slicing_keeps_growth_for_the_oldest_returned_year(self):
        rows = [fact("Revenues", f"{year}-12-31", 100.0 + year) for year in range(2020, 2025)]
        annual = metrics.annual_metrics(rows, years=2)
        self.assertEqual(len(annual), 2)
        self.assertIsNotNone(annual[-1]["revenue_yoy"])

    def test_periods_without_revenue_are_skipped(self):
        rows = [
            fact("Revenues", "2023-12-31", 100.0),
            fact("NetIncomeLoss", "2024-12-31", 10.0),
        ]
        self.assertEqual([row["fiscal_year"] for row in metrics.annual_metrics(rows)], [2023])

    def test_missing_tags_come_back_as_none(self):
        annual = metrics.annual_metrics([fact("Revenues", "2024-12-31", 100.0)])
        row = annual[0]
        self.assertIsNone(row["net_income"])
        self.assertIsNone(row["eps_diluted"])
        self.assertIsNone(row["operating_margin"])


if __name__ == "__main__":
    unittest.main()
