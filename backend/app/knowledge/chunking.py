"""Transcript chunking (PRD-M8 R3): ~500-token windows with small overlap,
carrying char offsets back into the raw transcript.

lesson transcripts are auto-captions: long, largely unpunctuated runs of words with
few sentence boundaries. Rather than depend on a tokenizer, we chunk on
whitespace-delimited words using an approximate tokens≈words heuristic (English
prose averages ~1.3 tokens/word; 500 tokens ≈ ~380 words). Each chunk records
the character span in the original text so the reader (M8b) can anchor highlights.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ~500 tokens ≈ ~380 words for this corpus; 60-word overlap keeps context across
# the boundary so a setup explained across two windows is still retrievable.
DEFAULT_WORDS_PER_CHUNK = 380
DEFAULT_OVERLAP_WORDS = 60

_WORD_RE = re.compile(r"\S+")


@dataclass
class Chunk:
    index: int        # 0-based chunk number within the lesson
    text: str
    char_start: int
    char_end: int


def chunk_text(
    text: str,
    words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> list[Chunk]:
    """Split `text` into overlapping word-windows with char offsets.

    Returns [] for empty/whitespace-only text. The last window is emitted even
    if short. Overlap must be < window size.
    """
    if overlap_words >= words_per_chunk:
        raise ValueError("overlap_words must be smaller than words_per_chunk")

    # (word, start, end) spans over the original string
    spans = [(m.group(0), m.start(), m.end()) for m in _WORD_RE.finditer(text)]
    if not spans:
        return []

    chunks: list[Chunk] = []
    step = words_per_chunk - overlap_words
    idx = 0
    i = 0
    n = len(spans)
    while i < n:
        window = spans[i : i + words_per_chunk]
        char_start = window[0][1]
        char_end = window[-1][2]
        chunk_str = text[char_start:char_end]
        chunks.append(Chunk(index=idx, text=chunk_str, char_start=char_start, char_end=char_end))
        idx += 1
        if i + words_per_chunk >= n:
            break
        i += step
    return chunks
