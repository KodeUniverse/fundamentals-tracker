"""Extract the 10-K narrative sections.

Two filings per company: the latest annual report is compared against the prior
one, so both are stored whole and compared as contiguous text.

As with `facts.py`, the caller constructs one `Company` per ticker per cycle.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import edgar

from rows import SectionRow

FILINGS_PER_COMPANY = 2

# Local section name -> TenK attribute. Both return plain str, or None if the
# filing does not have the item.
_SECTION_ATTRIBUTES = {
    "risk_factors": "risk_factors",
    "mda": "management_discussion",
}

# A parsed section shorter than this is a cross-reference to somewhere else in
# the document, not the section body. Eaton's Item 7 is a 252-character pointer.
_STUB_CHARS = 2000

# EDGAR's HTML-to-text keeps typographic quotes, and ETN's MD&A heading uses a
# curly apostrophe that a straight-quote pattern would miss.
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
_SPACE_RUN = re.compile(r"[ \t\u00a0]+")
_BLANK_RUN = re.compile(r"\n{3,}")
_MDA_HEADING = re.compile(r"management's discussion and analysis", re.IGNORECASE)


def collect_sections(company: edgar.Company) -> list[SectionRow]:
    """Item 1A and Item 7 from the latest two unamended 10-K filings.

    Sorted newest filing first, matching `get_filings`, so `period_end` descends.
    """
    filings = company.get_filings(form="10-K", amendments=False).head(FILINGS_PER_COMPANY)
    rows: list[SectionRow] = []

    for filing in filings:
        tenk = filing.obj()
        for section in _SECTION_ATTRIBUTES:
            content = _section_text(tenk, section)
            if content is None:
                continue
            rows.append(
                SectionRow(
                    cik=int(company.cik),
                    accession=filing.accession_no,
                    form_type=filing.form,
                    filed_date=filing.filing_date,
                    period_end=date.fromisoformat(filing.period_of_report),
                    section=section,
                    content=content,
                    char_count=len(content),
                )
            )

    return rows


def _section_text(tenk: Any, section: str) -> str | None:
    text = getattr(tenk, _SECTION_ATTRIBUTES[section])
    if text is None:
        return None
    if len(text) < _STUB_CHARS and section == "mda":
        text = _mda_from_document(tenk) or text
    return _clean(text)


def _mda_from_document(tenk: Any) -> str | None:
    """Recover Item 7 when the filing points at it instead of containing it.

    Eaton's 10-K puts the real MD&A later in the document and leaves Item 7 as a
    one-sentence cross-reference, so the parser returns the pointer. Taking the
    last heading hit skips the table of contents and the cross-reference itself.
    Nothing but more MD&A follows that heading, so the tail is taken whole.
    """
    document = tenk.document.text().translate(_QUOTES)
    headings = list(_MDA_HEADING.finditer(document))
    if not headings:
        return None
    return document[headings[-1].start():]


def _clean(text: str) -> str:
    """Collapse the padding that EDGAR's HTML-to-text leaves inside tables."""
    text = _SPACE_RUN.sub(" ", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()
