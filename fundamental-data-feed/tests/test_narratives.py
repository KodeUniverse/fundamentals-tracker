"""Tests for narrative chunking. Needs the feed venv (narratives imports edgar).

Run from the feed directory: .venv/bin/python -m unittest discover -v
"""

import unittest

import narratives


def paragraph(index: int) -> str:
    return f"Paragraph {index}. " + ("word " * 60).strip()


class SplitChunksTest(unittest.TestCase):
    def setUp(self):
        self.text = "\n\n".join(paragraph(i) for i in range(30))
        self.chunks = narratives.split_chunks(self.text)

    def test_no_chunk_exceeds_the_size_limit(self):
        for chunk in self.chunks:
            self.assertLessEqual(len(chunk), narratives.CHUNK_CHARS)

    def test_every_paragraph_survives_somewhere(self):
        joined = "\n".join(self.chunks)
        for i in range(30):
            self.assertIn(paragraph(i), joined)

    def test_adjacent_chunks_overlap_so_a_cut_sentence_stays_retrievable(self):
        self.assertIn(self.chunks[0][-100:], self.chunks[1])

    def test_a_paragraph_longer_than_a_chunk_is_split_on_a_word_boundary(self):
        block = "sentence " * 1334  # ~12k characters, no blank lines
        chunks = narratives.split_chunks(block)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), narratives.CHUNK_CHARS)
            self.assertNotIn(" entence", chunk)  # never cut mid-word


class CleanTest(unittest.TestCase):
    def test_collapses_table_padding_and_blank_line_runs(self):
        dirty = "Item 7.\u00a0\u00a0 MD&A\n\n\n\nRevenue      1,234"
        self.assertEqual(narratives._clean(dirty), "Item 7. MD&A\n\nRevenue 1,234")


if __name__ == "__main__":
    unittest.main()
