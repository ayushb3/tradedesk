"""Embedder seam (frozen v1) + factory — the single swap point for turning text
into vectors, mirroring the `DataProvider` seam in `app/providers/base.py`.

Two implementations ship:

- `LocalEmbedder` — a local sentence-transformers model (default
  ``all-MiniLM-L6-v2``, 384-dim). No network, no API key. Heavy deps
  (torch, sentence-transformers) live in the optional
  ``backend/requirements-knowledge.txt``.
- `HashingEmbedder` — a dependency-free deterministic fallback (hashed
  bag-of-words → L2-normalised vector). Gives reasonable *lexical* retrieval so
  the ingestion + search pipeline stays fully exercisable when the heavy deps
  are not installed, and keeps tests hermetic and fast.

`get_embedder()` returns the local model when available and falls back to the
hashing embedder otherwise (or when ``KNOWLEDGE_EMBEDDER=hashing`` is set). Nothing
outside this module imports sentence-transformers.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol, runtime_checkable

# Default local model — small, fast, CPU-friendly, no API key.
DEFAULT_LOCAL_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HASHING_DIM = 512

_WORD_RE = re.compile(r"[a-z0-9']+")


@runtime_checkable
class Embedder(Protocol):
    """Turns a batch of texts into unit-norm float vectors.

    Contract:
    - `embed(texts)` returns one vector per input, each length `dim`.
    - Vectors are L2-normalised so cosine similarity == dot product.
    - `name` identifies the embedder (persisted on `knowledge_lessons.embedder` so a
      later re-ingest with a different model can detect the mismatch).
    """

    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


class HashingEmbedder:
    """Deterministic, dependency-free embedder.

    Hashes each token into a fixed-width vector (the hashing trick) with a
    sub-token n-gram signal, then L2-normalises. This is NOT semantic — it is a
    lexical bag-of-words approximation — but it is deterministic, needs no model
    download, and returns sensible top-k for keyword-ish queries, which is enough
    to exercise the full pipeline and to keep tests hermetic.
    """

    name = "hashing-v1"

    def __init__(self, dim: int = HASHING_DIM) -> None:
        self.dim = dim

    def _bucket(self, token: str) -> int:
        h = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "big") % self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            tokens = _WORD_RE.findall(text.lower())
            for tok in tokens:
                vec[self._bucket(tok)] += 1.0
                # light 3-gram signal so near-spellings share mass
                if len(tok) > 4:
                    for i in range(len(tok) - 2):
                        vec[self._bucket("#" + tok[i : i + 3])] += 0.3
            out.append(_l2_normalize(vec))
        return out


class LocalEmbedder:
    """sentence-transformers wrapper. Lazy-loads the model on first `embed`."""

    def __init__(self, model_name: str = DEFAULT_LOCAL_MODEL) -> None:
        self.name = f"st:{model_name.split('/')[-1]}"
        self._model_name = model_name
        self._model = None
        self.dim = 384  # all-MiniLM-L6-v2; refined once the model loads

    def _ensure(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # heavy, optional

            self._model = SentenceTransformer(self._model_name)
            self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure()
        vecs = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return [list(map(float, v)) for v in vecs]


def sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return False
    return True


def get_embedder(prefer: str | None = None) -> Embedder:
    """Return the active embedder.

    Selection order:
    1. explicit `prefer` arg, else `KNOWLEDGE_EMBEDDER` env var ("local" | "hashing").
    2. "local" when sentence-transformers is importable, else the hashing fallback.

    A missing heavy dep never raises here — it degrades to `HashingEmbedder`.
    """
    choice = (prefer or os.getenv("KNOWLEDGE_EMBEDDER") or "").strip().lower()
    if choice == "hashing":
        return HashingEmbedder()
    if choice == "local":
        return LocalEmbedder(os.getenv("KNOWLEDGE_EMBED_MODEL", DEFAULT_LOCAL_MODEL))
    # auto
    if sentence_transformers_available():
        return LocalEmbedder(os.getenv("KNOWLEDGE_EMBED_MODEL", DEFAULT_LOCAL_MODEL))
    return HashingEmbedder()
