"""Extract the 10-K narrative sections and split them for retrieval.

Two filings per company: the brief compares the latest annual report against the
prior one, so both are stored whole and compared as contiguous text. Chunks are
derived from the stored text and exist for ranked retrieval, not for storage.

As with `facts.py`, the caller constructs one `Company` per ticker per cycle.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, TypedDict

import edgar

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

# 4000 characters is roughly 1000 tokens of English prose. The overlap keeps a
# sentence that straddles a boundary retrievable from either side.
CHUNK_CHARS = 4000
CHUNK_OVERLAP = 400

# EDGAR's HTML-to-text keeps typographic quotes, and ETN's MD&A heading uses a
# curly apostrophe that a straight-quote pattern would miss.
_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
_SPACE_RUN = re.compile(r"[ \t\u00a0]+")
_BLANK_RUN = re.compile(r"\n{3,}")
_MDA_HEADING = re.compile(r"management's discussion and analysis", re.IGNORECASE)


class SectionRow(TypedDict):
    cik: int
    accession: str
    form_type: str
    filed_date: date
    period_end: date
    section: str
    content: str
    char_count: int


class ChunkRow(TypedDict):
    section_id: int
    chunk_index: int
    content: str
    token_count: int


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


def chunk_rows(section_id: int, content: str) -> list[ChunkRow]:
    """DB-ready chunk rows for one stored section."""
    return [
        ChunkRow(
            section_id=section_id,
            chunk_index=index,
            content=chunk,
            token_count=len(chunk) // 4,
        )
        for index, chunk in enumerate(split_chunks(content))
    ]


def split_chunks(content: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split a section into overlapping windows that end on word boundaries.

    Each returned chunk is at most `size` characters plus the carry-over tail
    from the previous chunk, so a boundary never lands mid-word.
    """
    chunks: list[str] = []
    current: list[str] = []
    length = 0

    for unit in _units(content, size):
        if current and length + len(unit) > size:
            chunks.append("\n\n".join(current))
            # Keep a tail for context, but never at the cost of a chunk that
            # cannot hold the next unit: a 3995-character paragraph leaves no
            # room for overlap.
            room = size - len(unit) - 2
            carry = _tail(current[-1], min(overlap, room)) if room > 0 else ""
            current = [carry] if carry else []
            length = len(carry) + 2
        current.append(unit)
        length += len(unit) + 2

    if current:
        chunks.append("\n\n".join(current))
    return chunks


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


def _units(text: str, size: int) -> list[str]:
    """Paragraphs, with any paragraph longer than `size` split on word breaks."""
    units: list[str] = []

    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        while len(paragraph) > size:
            cut = paragraph.rfind(" ", 0, size)
            if cut <= 0:
                cut = size
            units.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            units.append(paragraph)

    return units


def _tail(text: str, overlap: int) -> str:
    """Last `overlap` characters of a chunk, moved forward to a word boundary."""
    if overlap <= 0 or len(text) <= overlap:
        return text
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1:] if space != -1 else tail


def _clean(text: str) -> str:
    """Collapse the padding that EDGAR's HTML-to-text leaves inside tables."""
    text = _SPACE_RUN.sub(" ", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()
