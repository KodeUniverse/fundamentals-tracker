"""Narrative chunking. Needs the feed environment (narratives imports edgartools).

Run from the feed directory: uv run pytest
"""

import pytest

from sources import narratives


def paragraph(index: int) -> str:
    return f"Paragraph {index}. " + ("word " * 60).strip()


TEXT = "\n\n".join(paragraph(i) for i in range(30))
CHUNKS = narratives.split_chunks(TEXT)


def test_no_chunk_exceeds_the_size_limit():
    assert [len(chunk) for chunk in CHUNKS if len(chunk) > narratives.CHUNK_CHARS] == []


@pytest.mark.parametrize("index", range(30))
def test_every_paragraph_survives_somewhere(index):
    assert paragraph(index) in "\n".join(CHUNKS)


def test_adjacent_chunks_overlap_so_a_cut_sentence_stays_retrievable():
    assert CHUNKS[0][-100:] in CHUNKS[1]


def test_a_paragraph_longer_than_a_chunk_is_split_on_a_word_boundary():
    block = "sentence " * 1334  # ~12k characters, no blank lines
    chunks = narratives.split_chunks(block)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= narratives.CHUNK_CHARS
        assert " entence" not in chunk


def test_clean_collapses_table_padding_and_blank_line_runs():
    dirty = "Item 7.\u00a0\u00a0 MD&A\n\n\n\nRevenue      1,234"
    assert narratives._clean(dirty) == "Item 7. MD&A\n\nRevenue 1,234"
