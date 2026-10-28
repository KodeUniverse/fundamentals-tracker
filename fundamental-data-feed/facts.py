"""Fetch EDGAR company facts and normalize them to DB rows.

Plain functions over a `Company` instance rather than a class or a module-level
cache: the caller constructs one `Company` per ticker per cycle, so the lifecycle
is explicit and the mapping is easy to test. edgartools already caches the
expensive part (the parsed facts payload) internally.

The output shapes mirror `database/init.sql`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import NamedTuple, TypedDict

import edgar
from edgar.entity.models import FinancialFact


class Entity(NamedTuple):
    cik: int
    entity_name: str
    ticker: str | None


class ConceptRow(TypedDict):
    taxonomy: str
    name: str
    label: str | None


class FactRow(TypedDict):
    cik: int
    concept_id: int
    unit: str
    value: float
    start_date: date | None
    end_date: date
    form_type: str
    filed_date: date
    accession: str
    filing_fy: int | None
    filing_fp: str | None


def build_entity(company: edgar.Company, ticker: str) -> Entity:
    return Entity(cik=int(company.cik), entity_name=company.name, ticker=ticker)


def get_all_facts(company: edgar.Company) -> list[FinancialFact]:
    """Every reported fact for the company, across all filings and periods.

    Raw facts, not statement line items: the DB is a faithful mirror of what
    EDGAR reported, with statement/metric resolution happening above it.
    """
    entity_facts = company.get_facts()
    if not entity_facts:
        raise ValueError(f"No facts returned for {company.name} (CIK {company.cik})")
    return entity_facts.get_all_facts()


def collect_concepts(facts: Sequence[FinancialFact]) -> list[ConceptRow]:
    """Unique concepts across a company's facts.

    Deduplicated on the full `taxonomy:name` string EDGAR gives us, so the split
    runs once per concept instead of once per fact.
    """
    seen: set[str] = set()
    concepts: list[ConceptRow] = []

    for fact in facts:
        if fact.concept in seen:
            continue
        seen.add(fact.concept)
        taxonomy, name = fact.concept.split(":", 1)
        concepts.append(ConceptRow(taxonomy=taxonomy, name=name, label=fact.label))

    return concepts


def fact_rows(
    facts: Sequence[FinancialFact],
    cik: int,
    concept_ids: dict[str, int],
) -> list[FactRow]:
    """DB-ready fact rows; `concept_ids` maps `taxonomy:name` to its id.

    `frame` is not exposed by `FinancialFact`, so that column stays NULL.
    """
    return [
        FactRow(
            cik=cik,
            concept_id=concept_ids[fact.concept],
            unit=fact.unit,
            value=fact.numeric_value,
            start_date=fact.period_start,
            end_date=fact.period_end,
            form_type=fact.form_type,
            filed_date=fact.filing_date,
            accession=fact.accession,
            # 0 / "" mean EDGAR did not report a filing period
            filing_fy=fact.fiscal_year or None,
            filing_fp=fact.fiscal_period or None,
        )
        for fact in facts
    ]
