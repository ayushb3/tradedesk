"""Vector index seam — persisted store of chunk embeddings + metadata.

PRD-M8 §5.1 names ChromaDB (collection ``knowledge_playbook`` under
``data/knowledge_chroma/``) as the vector store, with the note that it is a
*rebuildable artifact, never the source of truth*. ChromaDB pulls in
``chroma-hnswlib`` (a C-extension) which needs a compiler toolchain that is not
always present, so this module keeps the store behind a small seam:

- `ChromaVectorIndex`  — used when the `chromadb` package is importable.
- `NumpyVectorIndex`   — a dependency-free fallback (numpy is already a core
  dep) that persists vectors + metadata to a single ``.npz``/JSON pair under
  ``data/knowledge_chroma/``. Brute-force cosine over a few thousand chunks is
  instant, so search stays fully functional without ChromaDB.

Both expose the same tiny interface (`upsert`, `query`, `delete_lesson`,
`count`, `is_empty`). The metadata schema per chunk is identical:
``{lesson_id, module, seq, title, char_start, char_end}`` (PRD R3).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

COLLECTION = "knowledge_playbook"


@dataclass
class ChunkHit:
    id: str
    text: str
    score: float  # cosine similarity in [-1, 1]
    metadata: dict


@runtime_checkable
class VectorIndex(Protocol):
    def upsert(
        self, ids: list[str], vectors: list[list[float]], texts: list[str],
        metadatas: list[dict],
    ) -> None: ...

    def delete_lesson(self, lesson_id: int) -> None: ...

    def query(self, vector: list[float], k: int) -> list[ChunkHit]: ...

    def count(self) -> int: ...

    def is_empty(self) -> bool: ...


# --------------------------------------------------------------------------- #
# Numpy fallback (no external service; persists to data/knowledge_chroma/)
# --------------------------------------------------------------------------- #
class NumpyVectorIndex:
    """Brute-force cosine index persisted as vectors.npy + records.jsonl.

    Vectors are stored L2-normalised (the embedders already normalise), so a
    single matrix-vector dot product yields cosine scores. Fine for the ~3–6k
    course chunks; if it ever grows, swap in ChromaVectorIndex with no caller change.
    """

    backend = "numpy"

    def __init__(self, persist_dir: str | Path) -> None:
        self.dir = Path(persist_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._vec_path = self.dir / "vectors.npy"
        self._rec_path = self.dir / "records.jsonl"
        self._vectors: np.ndarray | None = None
        self._records: list[dict] = []  # {id, text, metadata}
        self._load()

    def _load(self) -> None:
        if self._rec_path.exists():
            self._records = [
                json.loads(line) for line in self._rec_path.read_text(
                    encoding="utf-8"
                ).splitlines() if line.strip()
            ]
        if self._vec_path.exists():
            self._vectors = np.load(self._vec_path)
        else:
            self._vectors = None

    def _flush(self) -> None:
        with self._rec_path.open("w", encoding="utf-8") as fh:
            for r in self._records:
                fh.write(json.dumps(r) + "\n")
        if self._vectors is not None and len(self._records):
            np.save(self._vec_path, self._vectors)
        elif self._vec_path.exists():
            self._vec_path.unlink()

    def delete_lesson(self, lesson_id: int) -> None:
        keep = [
            i for i, r in enumerate(self._records)
            if r["metadata"].get("lesson_id") != lesson_id
        ]
        if len(keep) == len(self._records):
            return
        self._records = [self._records[i] for i in keep]
        self._vectors = self._vectors[keep] if self._vectors is not None and keep else None
        self._flush()

    def upsert(self, ids, vectors, texts, metadatas) -> None:
        if not ids:
            return
        # drop any existing ids first (idempotent upsert)
        idset = set(ids)
        keep = [i for i, r in enumerate(self._records) if r["id"] not in idset]
        recs = [self._records[i] for i in keep]
        vecs = self._vectors[keep] if self._vectors is not None and keep else None

        new = np.asarray(vectors, dtype=np.float32)
        self._vectors = new if vecs is None else np.vstack([vecs, new])
        self._records = recs + [
            {"id": i, "text": t, "metadata": m}
            for i, t, m in zip(ids, texts, metadatas)
        ]
        self._flush()

    def query(self, vector, k) -> list[ChunkHit]:
        if self._vectors is None or not len(self._records):
            return []
        q = np.asarray(vector, dtype=np.float32)
        sims = self._vectors @ q  # vectors already normalised
        k = min(k, len(self._records))
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [
            ChunkHit(
                id=self._records[i]["id"],
                text=self._records[i]["text"],
                score=float(sims[i]),
                metadata=self._records[i]["metadata"],
            )
            for i in idx
        ]

    def count(self) -> int:
        return len(self._records)

    def is_empty(self) -> bool:
        return self.count() == 0


# --------------------------------------------------------------------------- #
# ChromaDB backend (preferred when installed)
# --------------------------------------------------------------------------- #
class ChromaVectorIndex:
    backend = "chromadb"

    def __init__(self, persist_dir: str | Path) -> None:
        import chromadb  # optional, heavy

        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._col = self._client.get_or_create_collection(
            name=COLLECTION, metadata={"hnsw:space": "cosine"}
        )

    def delete_lesson(self, lesson_id: int) -> None:
        self._col.delete(where={"lesson_id": lesson_id})

    def upsert(self, ids, vectors, texts, metadatas) -> None:
        if not ids:
            return
        self._col.upsert(
            ids=ids, embeddings=vectors, documents=texts, metadatas=metadatas
        )

    def query(self, vector, k) -> list[ChunkHit]:
        res = self._col.query(
            query_embeddings=[vector], n_results=k,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[ChunkHit] = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for i, doc, meta, dist in zip(ids, docs, metas, dists):
            hits.append(ChunkHit(id=i, text=doc, score=1.0 - float(dist), metadata=meta))
        return hits

    def count(self) -> int:
        return int(self._col.count())

    def is_empty(self) -> bool:
        return self.count() == 0


def chromadb_available() -> bool:
    try:
        import chromadb  # noqa: F401
    except Exception:
        return False
    return True


def get_vector_index(persist_dir: str | Path, prefer: str | None = None) -> VectorIndex:
    """Return a persisted vector index. Prefers ChromaDB when importable,
    else the numpy fallback. `prefer` ("chroma"|"numpy") or env KNOWLEDGE_VECTOR_BACKEND
    forces a backend (used by tests to stay hermetic)."""
    import os

    choice = (prefer or os.getenv("KNOWLEDGE_VECTOR_BACKEND") or "").strip().lower()
    if choice == "numpy":
        return NumpyVectorIndex(persist_dir)
    if choice in ("chroma", "chromadb"):
        return ChromaVectorIndex(persist_dir)
    if chromadb_available():
        return ChromaVectorIndex(persist_dir)
    return NumpyVectorIndex(persist_dir)
