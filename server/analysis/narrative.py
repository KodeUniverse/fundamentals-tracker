"""Paragraph-level comparison of two versions of a filing section.

Risk factors are the one question in the brief that is a set difference rather
than a retrieval match ("what did they add"), and two sections of 115,000
characters do not fit in a prompt. Splitting on the blank lines the ingest
preserved and comparing word sets is crude, but it runs in milliseconds and
returns paragraphs a person can check against the filing.
"""

from __future__ import annotations

import re

# Short blocks are headings, page furniture, or table fragments.
MIN_PARAGRAPH_CHARS = 120

# A paragraph counts as new when no paragraph from the prior year is more
# similar than this. Revised-with-changes paragraphs therefore show up as new,
# which is the safe direction for "what did they add".
SIMILARITY_THRESHOLD = 0.45

_WORDS = re.compile(r"[a-z]{3,}")


def paragraphs(text: str) -> list[str]:
    """Substantive paragraphs of a section, in document order."""
    return [block.strip() for block in text.split("\n\n") if len(block.strip()) >= MIN_PARAGRAPH_CHARS]


def words(text: str) -> frozenset[str]:
    return frozenset(_WORDS.findall(text.lower()))


def similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard overlap of two word sets, between 0 and 1."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def new_paragraphs(newer: str, older: str, threshold: float = SIMILARITY_THRESHOLD) -> list[str]:
    """Paragraphs in `newer` with no sufficiently similar match in `older`.

    Order is preserved so the result reads like the filing, not like a diff.
    """
    prior = [words(block) for block in paragraphs(older)]
    added: list[str] = []

    for block in paragraphs(newer):
        current = words(block)
        best = 0.0
        for other in prior:
            score = similarity(current, other)
            if score > best:
                best = score
                if best >= threshold:
                    break
        if best < threshold:
            added.append(block)

    return added
