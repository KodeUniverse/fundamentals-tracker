"""Split a filing section into overlapping windows for retrieval.

Pure text processing: no network, no database. Chunks exist so a question can be
answered from a few paragraphs instead of a whole section, and so each unit can
carry its own full-text index entry.
"""

from __future__ import annotations

from rows import ChunkRow

# 4000 characters is roughly 1000 tokens of English prose. The overlap keeps a
# sentence that straddles a boundary retrievable from either side.
CHUNK_CHARS = 4000
CHUNK_OVERLAP = 400


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
