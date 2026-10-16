"""build_debrief_context — assembles everything the interviewer knows BEFORE
asking the first question (data-eng task 6.0.3).

Pure function: no LLM calls, no side effects. Raises ValueError if the
trade_id is unknown (routes catch this and return 404).

Candles around the trade: ~30 bars before entry through ~10 bars after exit,
queried via provider.get_candles_range so both mock and live providers work.

Checklist template: loaded from the `checklist_template` DB table (all active
items ordered by sort_order). If the table is empty the field defaults to [].
There is no per-trade checklist template selection yet (that lands when
scenarios link trades to template ids — see PLAN-M6-TRAINING §3). Until then
the global active template is used, which is the right default for the form
backend's zero-credential path.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.connection import get_engine, init_db
from app.db.schema import checklist_template as checklist_t
from app.db.schema import fills as fills_t
from app.db.schema import trade_fills as trade_fills_t
from app.ingest.query import get_trade
from app.llm.base import DebriefContext
from app.models.agents import KnowledgeContext
from app.models.trades import Fill, Side
from app.providers.base import DataProvider

_ET = ZoneInfo("America/New_York")

# Bars of context to grab around the trade window.
_BARS_BEFORE = 30   # 1-minute bars before entry
_BARS_AFTER = 10    # 1-minute bars after exit (or after entry if still open)


def build_debrief_context(
    provider: DataProvider,
    trade_id: int,
    settings: Settings | None = None,
) -> DebriefContext:
    """Assemble the full debrief context for *trade_id*.

    Raises ValueError("trade not found") if the trade does not exist — routes
    should catch this and return HTTP 404 (matching the pattern in
    routes/trades.py where unknown ids raise 404 directly).

    Returns a DebriefContext with:
    - trade: fully hydrated Trade (tags + enrichment attached)
    - fills: the fills that made up this trade, ordered by ts asc
    - candles_around: ~30 bars before entry + ~10 bars after exit
    - enrichment: the trade's current TradeEnrichment (may be empty)
    - tags: the trade's current tags
    - checklist_template: all active checklist items from the DB, or []
    """
    settings = settings or get_settings()
    engine = get_engine(settings.db_path)
    init_db(engine)

    trade = get_trade(trade_id, engine=engine)
    if trade is None:
        raise ValueError("trade not found")

    # --- fills for this trade ----------------------------------------------- #
    fills = _load_fills_for_trade(trade_id, engine)

    # --- candles around the trade ------------------------------------------- #
    candles_around = _load_candles_around(provider, trade)

    # --- checklist template ------------------------------------------------- #
    checklist_template = _load_checklist_template(engine)

    return DebriefContext(
        trade=trade,
        fills=fills,
        candles_around=candles_around,
        enrichment=trade.enrichment,
        tags=trade.tags,
        checklist_template=checklist_template,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _load_fills_for_trade(trade_id: int, engine) -> list[Fill]:
    """Return the fills that belong to trade_id, ordered by ts asc."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(fills_t)
            .select_from(
                fills_t.join(trade_fills_t, fills_t.c.id == trade_fills_t.c.fill_id)
            )
            .where(trade_fills_t.c.trade_id == trade_id)
            .order_by(trade_fills_t.c.seq)
        ).fetchall()

    return [
        Fill(
            id=m["id"],
            provider_fill_id=m["provider_fill_id"],
            account=m["account"],
            symbol=m["symbol"],
            side=Side(m["side"]),
            qty=m["qty"],
            price=m["price"],
            ts=m["ts_utc"],
            fees=m["fees"],
            simulated=bool(m["simulated"]),
            raw_json=m["raw_json"],
        )
        for m in (r._mapping for r in rows)
    ]


def _load_candles_around(provider: DataProvider, trade) -> list:
    """Fetch candles from _BARS_BEFORE entry to _BARS_AFTER exit (or entry).

    Uses provider.get_candles_range so both MockProvider (synthetic) and
    WebullProvider (cached) work without extra DB queries. The window is
    widened by _BARS_BEFORE * 60 seconds before open_ts and _BARS_AFTER * 60
    seconds after close_ts (or open_ts when still open).
    """
    from datetime import timezone

    # Normalise to UTC-aware datetimes (trade timestamps are naive UTC from SQLite)
    def _utc(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    entry_ts = _utc(trade.open_ts)
    exit_ts = _utc(trade.close_ts) if trade.close_ts else entry_ts

    start = entry_ts - timedelta(minutes=_BARS_BEFORE)
    end = exit_ts + timedelta(minutes=_BARS_AFTER)

    try:
        return provider.get_candles_range(trade.symbol, start, end, "1m")
    except Exception:
        # Never block the debrief because candles are unavailable.
        # The FormProvider degrades gracefully when candles_around is empty.
        return []


def _load_checklist_template(engine) -> list[dict]:
    """Return active checklist items ordered by sort_order as dicts.

    Each dict: {"item": str, "checked": False} — the "checked" field matches
    the TradeEnrichment.checklist schema so the form backend can present the
    template as a starting point.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            select(checklist_t)
            .where(checklist_t.c.active.is_(True))
            .order_by(checklist_t.c.sort_order)
        ).fetchall()

    return [
        {"item": r._mapping["item"], "checked": False}
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# M9a — Trade Critic course context (issue #58, R2/R7)
# --------------------------------------------------------------------------- #


def _et_time_bucket(open_ts) -> str:
    """Bucket a trade's open timestamp into an ET time-of-day label.

    "open" 09:30–10:00, "morning" 10:00–11:30, "midday" 11:30–14:00,
    "afternoon" 14:00–16:00; "premarket"/"afterhours" outside RTH. Bucketed by
    the ET wall clock (converted via ZoneInfo), so DST is handled correctly.
    """
    if open_ts is None:
        return "midday"
    dt = open_ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et = dt.astimezone(_ET)
    minutes = et.hour * 60 + et.minute
    if minutes < 9 * 60 + 30:
        return "premarket"
    if minutes < 10 * 60:
        return "open"
    if minutes < 11 * 60 + 30:
        return "morning"
    if minutes < 14 * 60:
        return "midday"
    if minutes < 16 * 60:
        return "afternoon"
    return "afterhours"


def build_knowledge_context(
    trade,
    tags,
    detected_patterns,
    *,
    k: int = 8,
    engine=None,
    embedder=None,
    index=None,
) -> KnowledgeContext:
    """Assemble the curriculum half of the critic's context (R2/R7).

    Builds a deterministic query from the trade's setup-tag names, the detected
    patterns, and an ET time-of-day bucket, then runs ONE `retrieve()` call — no
    tool loop, code-not-tools (R7). Never raises: when retrieval is unavailable
    (KB not ingested, embeddings absent), returns `available=False` with empty
    `hits`/`concepts` so the critic reports `unclear` rather than fabricating
    citations.
    """
    # Import here (not at module top) to avoid a hard import cycle:
    # app.agents.retrieve -> app.knowledge.* is independent, but keeping the import
    # local mirrors the lazy-seam convention used elsewhere in this file.
    from app.agents.retrieve import retrieve

    tag_names = [t.name for t in (tags or [])]
    patterns = list(detected_patterns or [])
    bucket = _et_time_bucket(getattr(trade, "open_ts", None))

    parts = [*tag_names, *patterns, f"{bucket} session"]
    # Dedupe while preserving order; a natural-language query the embedder reads.
    seen: set[str] = set()
    terms: list[str] = []
    for p in parts:
        p = (p or "").strip()
        if not p or p.lower() in seen:
            continue
        seen.add(p.lower())
        terms.append(p)
    query = ", ".join(terms) if terms else f"{bucket} session"

    resp = retrieve(
        query, k, expand_concepts=True, engine=engine, embedder=embedder, index=index
    )

    if not resp.available:
        return KnowledgeContext(
            available=False, status=resp.status, query=query, hits=[], concepts=[]
        )

    return KnowledgeContext(
        available=resp.available,
        status=resp.status,
        query=query,
        hits=list(resp.hits),
        concepts=[c.slug for c in resp.concepts],
    )
