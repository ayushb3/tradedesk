"""Stable trade identity (issue #3).

A trade's ``trade_key`` is derived from its OPENING fill's ``provider_fill_id``.
That id is immutable and unique per trade, so the key survives the
delete-and-rebuild in ``ingest.imports._rebuild_trades``. The key is hashed so
raw broker order ids (``rh-<order id>``) never leave the database.
"""
from __future__ import annotations

import hashlib

TRADE_KEY_PREFIX = "tk-"


def make_trade_key(opening_provider_fill_id: str | None) -> str | None:
    """Opaque stable key for a trade, or None if the opening fill has no id."""
    if not opening_provider_fill_id:
        return None
    digest = hashlib.sha256(opening_provider_fill_id.encode("utf-8")).hexdigest()
    return TRADE_KEY_PREFIX + digest[:20]
