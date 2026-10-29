"""Paragraph diff used by the year-over-year risk factor tool.

Run from the server directory: uv run pytest
"""

from analysis import narrative

OLD = "Supply chain disruption could raise costs and delay shipments to customers " * 4
KEPT = "Competition from other cloud providers could reduce our market share and margins " * 4
NEW = "Regulation of artificial intelligence in the European Union could require changes to our models " * 4


def test_short_blocks_are_dropped():
    text = "Item 1A. Risk Factors\n\n" + OLD
    assert narrative.paragraphs(text) == [OLD.strip()]


def test_paragraphs_keep_document_order():
    text = f"{OLD}\n\n{KEPT}"
    assert narrative.paragraphs(text) == [OLD.strip(), KEPT.strip()]


def test_added_paragraph_is_detected():
    older = f"{OLD}\n\n{KEPT}"
    newer = f"{OLD}\n\n{KEPT}\n\n{NEW}"

    added = narrative.new_paragraphs(newer, older)

    assert len(added) == 1
    assert "European Union" in added[0]


def test_identical_sections_add_nothing():
    section = f"{OLD}\n\n{KEPT}"
    assert narrative.new_paragraphs(section, section) == []


def test_reordered_paragraphs_are_not_new():
    older = f"{OLD}\n\n{KEPT}"
    newer = f"{KEPT}\n\n{OLD}"
    assert narrative.new_paragraphs(newer, older) == []


def test_identical_text_scores_one():
    words = narrative.words("risk factors and supply chain disruption")
    assert narrative.similarity(words, words) == 1.0


def test_unrelated_text_scores_zero():
    left = narrative.words("tariffs and supply chains")
    right = narrative.words("artificial intelligence regulation")
    assert narrative.similarity(left, right) == 0.0
