"""Seed concept vocabulary loader + idempotent DB seeder (M8c, PR-1).

The concept index is a *controlled vocabulary*: a seed JSON file is the source
of truth for which concepts may exist. The bundled ``seed_concepts.example.json``
holds a small generic set; point ``TRADEDESK_SEED_CONCEPTS`` at your own JSON file
(same shape) to use a vocabulary built from your course material. The LLM
extraction pass is drift-guarded to these slugs so it can never invent a concept
outside the vocabulary (PRD-M8 section 7 risk #2).

`load_seed()` reads the JSON; `seed_concepts_into_db()` upserts every seeded
concept (seeded=True) and every seed link idempotently, so re-running is safe.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from app.knowledge import store

_EXAMPLE_SEED_PATH = Path(__file__).resolve().parent / "seed_concepts.example.json"


def seed_path() -> Path:
    """``TRADEDESK_SEED_CONCEPTS`` (a local path) if set, else the bundled example."""
    env = os.getenv("TRADEDESK_SEED_CONCEPTS")
    return Path(env) if env else _EXAMPLE_SEED_PATH


@dataclass
class SeedVocab:
    """Parsed seed vocabulary.

    concepts: {slug: {slug, name, category, definition, aliases}}
    links:    [{a, b, relation}]
    """

    concepts: dict[str, dict]
    links: list[dict]

    def slugs(self) -> set[str]:
        """The allowed concept slug set — the drift-guard vocabulary."""
        return set(self.concepts.keys())


def load_seed(path: Path | None = None) -> SeedVocab:
    """Load and parse the seed vocabulary JSON (defaults to `seed_path()`)."""
    p = path or seed_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    concepts = {c["slug"]: c for c in data.get("concepts", [])}
    links = list(data.get("seed_links", []))
    return SeedVocab(concepts=concepts, links=links)


def seed_concepts_into_db(engine: Engine | None = None) -> int:
    """Idempotently upsert every seeded concept + seed link into the DB.

    Returns the number of concepts upserted. Safe to call repeatedly (upserts
    key on the unique slug; links skip duplicates).
    """
    seed = load_seed()
    n = 0
    for slug, fields in seed.concepts.items():
        store.upsert_concept(
            slug=slug,
            name=fields["name"],
            category=fields["category"],
            definition=fields.get("definition", ""),
            aliases=fields.get("aliases", []),
            seeded=True,
            engine=engine,
        )
        n += 1

    pairs = [
        {"a_slug": link["a"], "b_slug": link["b"], "relation": link["relation"]}
        for link in seed.links
    ]
    store.upsert_concept_links(pairs, engine=engine)
    return n
