"""Live fill import + FIFO round-trip trade building (M3).

`import_fills(provider)` pulls executions from a `DataProvider`, upserts them into
the `fills` table (deduped on `provider_fill_id`, `simulated=False`) and then
**rebuilds every non-simulated round-trip trade from scratch** off the full set of
non-simulated fills. The rebuild is idempotent: existing `simulated=False` trades
and their `trade_fills` links are deleted first, so re-running the import always
yields the same set of trades. Simulated (replay) trades are never touched.

Round-trip building is a per-symbol FIFO walk over fills in `ts` order:

- A trade opens when the signed position leaves flat (a buy-from-flat opens a
  **long**, a sell-from-flat opens a **short**) and closes when the position
  returns to flat.
- **Scale-in / scale-out**: adds in the position's direction are entry legs,
  reductions are exit legs. A trade is only emitted once the position fully
  flattens, so partial closes accumulate into one round-trip.
- **Cross-through-zero**: a fill that flips the sign of the position is split —
  the portion that brings the position to flat becomes the closing exit leg of
  the current trade, and the remainder opens a new opposite-direction trade as
  its first entry leg. Fees on a split fill are apportioned pro-rata by quantity.

P&L math:
- ``gross_pnl`` is computed by **FIFO lot-matching**: each exit lot is matched
  against the oldest still-open entry lot(s) in chronological order.  This
  matches how Webull (and most brokers) report realized P&L and avoids the
  average-cost artefact that produces fractional-cent amounts on whole-contract
  option trades.
- ``avg_entry`` / ``avg_exit`` are qty-weighted averages, kept as display fields.
- ``pnl_pct`` is a per-share ratio on avg_entry (multiplier-invariant display).
- All money is rounded **once** at the end (not per lot) to avoid penny drift.
"""
from __future__ import annotations

import hashlib
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.trade_key import make_trade_key
from app.db.schema import fills as fills_t
from app.db.schema import trade_enrichment as enrich_t
from app.db.schema import trade_fills as trade_fills_t
from app.db.schema import trade_tags as trade_tags_t
from app.db.schema import trades as trades_t
from app.models.trades import Direction, Fill, TradeStatus

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Fills imported from a broker export file (not the live provider) carry this
# provider_fill_id prefix. A live provider's full refresh and its incremental
# cursor leave them alone: they are another broker's history, not stale data.
EXPORT_FILL_PREFIXES = ("rh-",)


class MixedFillSourcesError(ValueError):
    """Raised when demo (mock) fills would be merged into real fills."""


def _not_export_fill():
    """SQL filter: fill did not come from a broker export file."""
    pfid = fills_t.c.provider_fill_id
    return or_(pfid.is_(None), and_(*(~pfid.like(p + "%") for p in EXPORT_FILL_PREFIXES)))


def _to_naive_utc(value) -> datetime:
    """Normalize a datetime (naive assumed UTC, or tz-aware) to naive UTC."""
    if not isinstance(value, datetime):
        # Delegate string parsing to the same rules sim.py uses.
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        value = datetime.fromisoformat(s)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _session_date(open_ts_naive_utc: datetime):
    """ET trading date for a naive-UTC open timestamp."""
    return open_ts_naive_utc.replace(tzinfo=UTC).astimezone(ET).date()


def _latest_fill_ts(conn) -> datetime | None:
    """Max ts_utc across non-simulated fills, as tz-aware UTC. None if empty.

    Used as the incremental-sync cursor: on a refresh we only ask the provider
    for fills newer than this, instead of re-walking the full history.
    """
    row = conn.execute(
        select(func.max(fills_t.c.ts_utc))
        .where(fills_t.c.simulated.is_(False))
        .where(_not_export_fill())
    ).scalar()
    return row.replace(tzinfo=timezone.utc) if row is not None else None


def _has_mock_fills(conn) -> bool:
    """True if any stored non-sim fill is mock-origin (provider_fill_id like
    'mock-%'). Mock fills are stored simulated=False, so on the first live
    import they must be wiped even though the table is non-empty — otherwise
    incremental sync would leave demo data masquerading as real trades."""
    row = conn.execute(
        select(fills_t.c.id)
        .where(fills_t.c.simulated.is_(False))
        .where(fills_t.c.provider_fill_id.like("mock-%"))
        .limit(1)
    ).first()
    return row is not None


def _has_real_fills(conn) -> bool:
    """True if any stored non-sim fill is not mock-origin."""
    row = conn.execute(
        select(fills_t.c.id)
        .where(fills_t.c.simulated.is_(False))
        .where(or_(fills_t.c.provider_fill_id.is_(None),
                   ~fills_t.c.provider_fill_id.like("mock-%")))
        .limit(1)
    ).first()
    return row is not None


# --------------------------------------------------------------------------- #
# Fill upsert
# --------------------------------------------------------------------------- #
def _synthetic_fill_id(f: Fill, dup_index: int) -> str:
    """Deterministic id for a fill the provider sent without one.

    Hash of the execution facts plus its index among identical fills in the same
    batch, so re-importing the same fills yields the same ids (no duplicates).
    """
    facts = "|".join(str(x) for x in (
        f.account, f.symbol, f.side.value, float(f.qty), float(f.price),
        _to_naive_utc(f.ts).isoformat(), f.option_type, f.strike, f.expiry, dup_index,
    ))
    return "nid-" + hashlib.sha1(facts.encode()).hexdigest()[:20]


def _upsert_fills(conn, provider_fills: list[Fill]) -> int:
    """Insert any not-yet-seen non-simulated fills. Returns count inserted.

    Dedupe key is `provider_fill_id`. Fills without one get a deterministic
    synthetic id (`_synthetic_fill_id`) so a re-import cannot duplicate them.
    """
    existing = {
        r[0]
        for r in conn.execute(
            select(fills_t.c.provider_fill_id).where(
                fills_t.c.provider_fill_id.is_not(None)
            )
        ).fetchall()
    }
    inserted = 0
    seen_facts: dict[str, int] = {}
    for f in provider_fills:
        pfid = f.provider_fill_id
        if pfid is None:
            base = _synthetic_fill_id(f, 0)
            dup = seen_facts.get(base, 0)
            seen_facts[base] = dup + 1
            pfid = base if dup == 0 else _synthetic_fill_id(f, dup)
        if pfid in existing:
            continue
        conn.execute(
            fills_t.insert().values(
                provider_fill_id=pfid,
                account=f.account,
                symbol=f.symbol,
                side=f.side.value,
                qty=float(f.qty),
                price=float(f.price),
                ts_utc=_to_naive_utc(f.ts),
                fees=float(f.fees or 0.0),
                multiplier=float(getattr(f, "multiplier", 1.0)),
                option_type=getattr(f, "option_type", None),
                strike=getattr(f, "strike", None),
                expiry=getattr(f, "expiry", None),
                simulated=False,
                raw_json=f.raw_json,
            )
        )
        existing.add(pfid)
        inserted += 1
    return inserted


# --------------------------------------------------------------------------- #
# FIFO round-trip builder
# --------------------------------------------------------------------------- #
def _contract_key(row) -> tuple:
    """Grouping key for the FIFO builder.

    Options: (symbol, option_type, strike, expiry) — each tuple element is
    canonical and hashable.  A buy of SPXW 7500C and a sell of SPXW 7475P share
    the same ticker but differ in strike/type, so they produce separate keys and
    are FIFO'd independently.

    Stocks (no option fields): (symbol, None, None, None) — falls back to
    ticker-only grouping, which is the correct behaviour for equities.
    """
    m = row if isinstance(row, dict) else row
    return (
        m.get("symbol"),
        m.get("option_type"),
        m.get("strike"),
        m.get("expiry"),
    )


def _build_contract_label(symbol: str, option_type: str | None,
                           strike: float | None, expiry: str | None) -> str:
    """Human-readable instrument label for the trades table.

    Options  → "SPXW 7500C 07/09"  (strike as int when whole, else float)
    Stocks   → "SPXW"  (ticker only)

    The MM/DD portion is derived from the ISO expiry string (first 10 chars).
    """
    if option_type is None:
        return symbol  # stock — ticker only

    # "CALL" -> "C", "PUT" -> "P" (anything else kept as-is for resilience)
    cp = "C" if str(option_type).upper() == "CALL" else "P"

    # Render strike: integer when whole (e.g. 7500 not 7500.0), float otherwise.
    if strike is not None:
        strike_str = str(int(strike)) if strike == int(strike) else str(strike)
    else:
        strike_str = "?"

    # Expiry: take first 10 chars of ISO string (YYYY-MM-DD), reformat as MM/DD.
    if expiry and len(expiry) >= 10:
        parts = expiry[:10].split("-")
        exp_str = f"{parts[1]}/{parts[2]}" if len(parts) == 3 else expiry[:10]
    else:
        exp_str = expiry or "?"

    return f"{symbol} {strike_str}{cp} {exp_str}"



class _TradeAcc:
    """Accumulates the legs of one flat-to-flat round-trip."""

    def __init__(self, direction: Direction) -> None:
        self.direction = direction
        # legs: (qty, price, ts, fill_id, fees)
        self.entries: list[tuple[float, float, datetime, int, float]] = []
        self.exits: list[tuple[float, float, datetime, int, float]] = []
        self.fill_seq: list[int] = []  # participating fill ids, first-seen order
        self.max_size: float = 0.0
        # options contract multiplier: first-seen fill's value wins (all fills in
        # one round-trip share the same symbol, so multiplier is constant).
        self.multiplier: float = 1.0

    def _touch(self, fill_id: int) -> None:
        if fill_id not in self.fill_seq:
            self.fill_seq.append(fill_id)

    def add_entry(self, qty, price, ts, fill_id, fees) -> None:
        self.entries.append((qty, price, ts, fill_id, fees))
        self._touch(fill_id)

    def add_exit(self, qty, price, ts, fill_id, fees) -> None:
        self.exits.append((qty, price, ts, fill_id, fees))
        self._touch(fill_id)


def _wavg(legs) -> tuple[float, float]:
    """(total_qty, qty-weighted avg price) for a list of legs."""
    tq = sum(l[0] for l in legs)
    if tq == 0:
        return 0.0, 0.0
    return tq, sum(l[0] * l[1] for l in legs) / tq


def _finalize(acc: _TradeAcc) -> dict:
    """Turn an accumulator into a dict of `trades` column values + fill order.

    gross_pnl is computed by FIFO lot-matching (not average-cost) so that
    scale-in trades with different entry prices produce the same P&L as a
    broker's FIFO realized-P&L report.  avg_entry / avg_exit are kept as
    display fields (qty-weighted averages).  Round once at the very end to
    avoid penny drift across matched lots.
    """
    entry_qty, avg_entry_raw = _wavg(acc.entries)
    avg_entry = round(avg_entry_raw, 4)
    qty = acc.max_size

    open_ts = min(l[2] for l in acc.entries)
    fees = round(sum(l[4] for l in acc.entries) + sum(l[4] for l in acc.exits), 2)

    is_long = acc.direction == Direction.long

    if acc.exits:
        _, avg_exit_raw = _wavg(acc.exits)
        avg_exit = round(avg_exit_raw, 4)
        close_ts = max(l[2] for l in acc.exits)
    else:
        avg_exit_raw = None
        avg_exit = None
        close_ts = None

    closed = avg_exit is not None
    if closed:
        # ------------------------------------------------------------------ #
        # FIFO lot-matching for gross_pnl                                     #
        #                                                                      #
        # Build an open-lots queue from the entry legs in chronological order  #
        # (entries are already in ts order because _build_symbol processes      #
        # fills in time order).  Each lot is [remaining_qty, price].            #
        # For each exit leg, drain from the front of the queue, accumulating    #
        # realized P&L per matched sub-lot.  Round the total once at the end.  #
        # ------------------------------------------------------------------ #
        open_lots: deque[list[float]] = deque(
            [e[0], e[1]] for e in acc.entries  # [qty, price]
        )
        raw_pnl = 0.0
        # sign: long profits when exit > entry; short profits when entry > exit.
        sign = 1.0 if is_long else -1.0

        for exit_qty, exit_px, _, _, _ in acc.exits:
            remaining = exit_qty
            while remaining > 1e-12 and open_lots:
                lot = open_lots[0]  # [lot_qty, lot_px]  (mutable list)
                matched = min(remaining, lot[0])
                raw_pnl += sign * (exit_px - lot[1]) * matched * acc.multiplier
                lot[0] -= matched
                if lot[0] <= 1e-12:
                    open_lots.popleft()
                remaining -= matched
            # remaining > 0 here means more exit qty than open lots — should
            # not happen in a well-formed round-trip, but handle gracefully:
            # the unmatched exit qty contributes 0 additional P&L.

        gross_pnl = round(raw_pnl, 2)
        net_pnl = round(gross_pnl - fees, 2)

        # pnl_pct: per-share ratio on avg_entry — multiplier-invariant display.
        # Use the FIFO gross (pre-multiplier per-share equivalent) so the sign
        # is always consistent.  For single-lot trades FIFO == avg-cost so this
        # is unchanged; for multi-lot trades it's an approximation that is
        # acceptable for a display-only field.
        if avg_entry_raw and qty and acc.multiplier:
            pnl_per_share_display = raw_pnl / (qty * acc.multiplier)
            pnl_pct = round((pnl_per_share_display / avg_entry_raw) * 100, 4)
        else:
            pnl_pct = None

        hold_time_s = int((close_ts - open_ts).total_seconds())
        status = TradeStatus.closed
    else:
        gross_pnl = net_pnl = pnl_pct = hold_time_s = None
        status = TradeStatus.open

    return {
        "row": {
            "symbol": None,  # filled in by caller (per-contract loop)
            "contract_label": None,  # filled in by caller
            "direction": acc.direction.value,
            "open_ts_utc": open_ts,
            "close_ts_utc": close_ts,
            "session_date": _session_date(open_ts),
            "qty": qty,
            "avg_entry": avg_entry,
            "avg_exit": avg_exit,
            "gross_pnl": gross_pnl,
            "fees": fees,
            "net_pnl": net_pnl,
            "pnl_pct": pnl_pct,
            "stop_price": None,
            "r_multiple": None,
            "hold_time_s": hold_time_s,
            "status": status.value,
            "simulated": False,
        },
        "fill_seq": list(acc.fill_seq),
    }


def _build_symbol(rows) -> list[dict]:
    """FIFO round-trips for one symbol. `rows` are fill mappings in ts order."""
    trades: list[dict] = []
    position = 0.0  # signed
    acc: _TradeAcc | None = None

    for m in rows:
        fid = m["id"]
        qty = float(m["qty"])
        price = float(m["price"])
        ts = m["ts_utc"]
        fees = float(m["fees"] or 0.0)
        fill_multiplier = float(m.get("multiplier") or 1.0)
        signed = qty if m["side"] == "buy" else -qty
        per_share_fee = (fees / qty) if qty else 0.0

        remaining = qty  # qty of this fill still to apply
        while remaining > 1e-12:
            if position == 0.0 or acc is None:
                # Open a fresh trade with the (rest of the) fill as an entry.
                acc = _TradeAcc(Direction.long if signed > 0 else Direction.short)
                acc.multiplier = fill_multiplier  # first-seen fill sets the multiplier
                acc.add_entry(remaining, price, ts, fid, round(per_share_fee * remaining, 6))
                position += remaining if signed > 0 else -remaining
                acc.max_size = max(acc.max_size, abs(position))
                remaining = 0.0
                continue

            same_dir = (position > 0 and signed > 0) or (position < 0 and signed < 0)
            if same_dir:
                # Scale-in: whole remainder adds to the position.
                acc.add_entry(remaining, price, ts, fid, round(per_share_fee * remaining, 6))
                position += remaining if signed > 0 else -remaining
                acc.max_size = max(acc.max_size, abs(position))
                remaining = 0.0
            else:
                # Reducing / closing the current position.
                closing = min(remaining, abs(position))
                acc.add_exit(closing, price, ts, fid, round(per_share_fee * closing, 6))
                position += closing if signed > 0 else -closing
                remaining -= closing
                if abs(position) <= 1e-12:
                    position = 0.0
                    trades.append(_finalize(acc))
                    acc = None
                    # Any leftover `remaining` opens an opposite-direction trade
                    # on the next loop iteration (cross-through-zero split).

    if acc is not None:
        trades.append(_finalize(acc))  # still-open tail
    return trades


def _opening_fill_id(fill_seq: list[int]) -> int | None:
    """The opening (first-seen) fill id of a trade, or None if it has no fills.

    `fill_seq` is stored in first-seen order (seq=0 is the opening entry), so the
    head of the list is the opening fill. Its `provider_fill_id` is the stable
    trade identity used to carry enrichment across a rebuild.
    """
    return fill_seq[0] if fill_seq else None


def _snapshot_enrichment(conn) -> dict[str, dict]:
    """Snapshot per-trade enrichment keyed by a STABLE `trade_key`.

    Called BEFORE the delete in `_rebuild_trades`. For every existing non-sim
    trade we capture its user-entered enrichment — ``stop_price``, the
    ``trade_enrichment`` row (notes/grade/checklist/screenshots) and its
    ``trade_tags`` — keyed by ``trade_key`` = the ``provider_fill_id`` of the
    trade's opening fill (seq=0 in ``trade_fills``). That key is immutable and
    unique per trade, so it survives the delete-and-rebuild and lets us
    re-attach the enrichment to the freshly built trade in
    `_restore_enrichment`.

    ``trade_key`` is read from the ``trades.trade_key`` column (a hash of that
    provider_fill_id, see ``app.db.trade_key``), so the snapshot still works
    after a full refresh has wiped the fills. Trades without a key (opening
    fill had no ``provider_fill_id``) are skipped.

    ``r_multiple`` is intentionally NOT snapshotted: it is derived from
    ``stop_price`` and the (possibly changed) avg entry/exit of the rebuilt
    trade, so it is recomputed on restore.
    """
    snapshot: dict[str, dict] = {}

    keyed = conn.execute(
        select(trades_t.c.id, trades_t.c.trade_key).where(
            trades_t.c.simulated.is_(False), trades_t.c.trade_key.is_not(None)
        )
    ).fetchall()
    for trade_id, trade_key in keyed:
        trade_row = conn.execute(
            select(trades_t.c.stop_price).where(trades_t.c.id == trade_id)
        ).first()
        stop_price = trade_row[0] if trade_row is not None else None

        enrich_row = conn.execute(
            select(
                enrich_t.c.notes,
                enrich_t.c.grade,
                enrich_t.c.checklist_json,
                enrich_t.c.screenshots_json,
            ).where(enrich_t.c.trade_id == trade_id)
        ).first()

        tag_ids = [
            r[0]
            for r in conn.execute(
                select(trade_tags_t.c.tag_id).where(
                    trade_tags_t.c.trade_id == trade_id
                )
            ).fetchall()
        ]

        # Skip trades with nothing worth carrying over.
        has_enrichment = enrich_row is not None and any(
            v is not None for v in enrich_row
        )
        if stop_price is None and not has_enrichment and not tag_ids:
            continue

        snapshot[trade_key] = {
            "stop_price": stop_price,
            "enrichment": (
                {
                    "notes": enrich_row[0],
                    "grade": enrich_row[1],
                    "checklist_json": enrich_row[2],
                    "screenshots_json": enrich_row[3],
                }
                if has_enrichment
                else None
            ),
            "tag_ids": tag_ids,
        }
    return snapshot


def _snapshot_trade_ids(conn) -> dict[str, int]:
    """Map trade_key -> existing integer id for every keyed non-sim trade."""
    return {
        key: tid
        for tid, key in conn.execute(
            select(trades_t.c.id, trades_t.c.trade_key).where(
                trades_t.c.simulated.is_(False), trades_t.c.trade_key.is_not(None)
            )
        ).fetchall()
    }


def _next_free_trade_id(conn) -> int:
    """First id above every trade id ever referenced, so a vanished trade's id
    is never handed to a different trade (which would adopt its debriefs)."""
    top = conn.execute(select(func.max(trades_t.c.id))).scalar() or 0
    for tbl, col in (
        ("debriefs", "trade_id"),
        ("scenarios", "source_trade_id"),
        ("trade_enrichment", "trade_id"),
        ("trade_tags", "trade_id"),
    ):
        v = conn.execute(text(f"SELECT MAX({col}) FROM {tbl}")).scalar()
        top = max(top, v or 0)
    for (ref,) in conn.execute(text("SELECT subject_ref FROM agent_sessions")).fetchall():
        if ref and ref.startswith("trade:") and ref[6:].isdigit():
            top = max(top, int(ref[6:]))
    return int(top) + 1


def _recompute_r_multiple(m, stop_price: float | None) -> float | None:
    """R-multiple from avg entry/exit and stop. None if not computable.

    Mirrors `app.ingest.query._recompute_r_multiple` exactly — the PATCH handler
    that first stored the stop used the same formula, so a rebuild must not drift
    from it. `m` is a mapping with avg_entry/avg_exit/direction.
    """
    avg_entry = m["avg_entry"]
    avg_exit = m["avg_exit"]
    if avg_entry is None or avg_exit is None or stop_price is None:
        return None
    risk_per_share = abs(avg_entry - stop_price)
    if risk_per_share == 0:
        return None
    is_long = m["direction"] == Direction.long.value
    pnl_per_share = (avg_exit - avg_entry) if is_long else (avg_entry - avg_exit)
    return round(pnl_per_share / risk_per_share, 4)


def _restore_enrichment(conn, trade_id: int, row: dict, snap: dict) -> None:
    """Re-attach snapshotted stop_price + enrichment + tags to a rebuilt trade.

    ``row`` is the freshly built trades-row dict (has avg_entry/avg_exit/direction),
    used to recompute ``r_multiple`` from the restored ``stop_price`` via the same
    formula the PATCH handler uses. The trader's stop is preserved as-is — no
    rejection of the stop==exit -> exactly -1R case (per issue #66); the stored
    data stays correct and a UI flag is left to the frontend.
    """
    stop_price = snap.get("stop_price")
    if stop_price is not None:
        r_multiple = _recompute_r_multiple(row, stop_price)
        conn.execute(
            trades_t.update()
            .where(trades_t.c.id == trade_id)
            .values(stop_price=stop_price, r_multiple=r_multiple)
        )

    enrichment = snap.get("enrichment")
    if enrichment is not None:
        conn.execute(enrich_t.insert().values(trade_id=trade_id, **enrichment))

    for tag_id in snap.get("tag_ids", []):
        conn.execute(
            trade_tags_t.insert().values(trade_id=trade_id, tag_id=tag_id)
        )


def _rebuild_trades(
    conn,
    snapshot: dict[str, dict] | None = None,
    key_ids: dict[str, int] | None = None,
) -> int:
    """Delete + rebuild all non-simulated trades from non-simulated fills.

    User-entered enrichment (stop_price, notes/grade/checklist/screenshots, tags)
    lives on or is keyed to the trade row, which this function deletes and
    rebuilds. To keep that enrichment durable across re-imports (issue #66) the
    caller snapshots it BEFORE any fill wipe keyed by a stable ``trade_key`` (the
    opening fill's provider_fill_id) and passes it in here; we re-attach it AFTER
    the rebuild by recomputing the same key for each new trade. When ``snapshot``
    is None we take it here (safe when no fills were wiped, e.g. an incremental
    refresh). ``r_multiple`` is recomputed from the restored stop rather than
    snapshotted, since the rebuilt trade's avgs may have shifted.

    **Stable ids (issue #3).** Each rebuilt trade whose ``trade_key`` matches an
    existing trade is re-inserted with that trade's OLD integer ``id``, so
    anything keyed on ``trades.id`` (debriefs, agent_sessions ``trade:<id>``,
    scenarios.source_trade_id, plans/day_fields later) stays attached. New
    trades get ids above every id ever referenced; ids are never recycled.
    ``key_ids`` (trade_key -> id) is snapshotted by the caller pre-wipe, or here.
    """
    if snapshot is None:
        snapshot = _snapshot_enrichment(conn)
    if key_ids is None:
        key_ids = _snapshot_trade_ids(conn)
    next_id = _next_free_trade_id(conn)

    # Delete existing non-sim trades and their fill links (idempotent rebuild).
    sim_false_ids = [
        r[0]
        for r in conn.execute(
            select(trades_t.c.id).where(trades_t.c.simulated.is_(False))
        ).fetchall()
    ]
    if sim_false_ids:
        conn.execute(
            trade_tags_t.delete().where(trade_tags_t.c.trade_id.in_(sim_false_ids))
        )
        conn.execute(
            enrich_t.delete().where(enrich_t.c.trade_id.in_(sim_false_ids))
        )
        conn.execute(
            trade_fills_t.delete().where(trade_fills_t.c.trade_id.in_(sim_false_ids))
        )
        conn.execute(trades_t.delete().where(trades_t.c.id.in_(sim_false_ids)))

    # Pull all non-simulated fills, ordered by contract key + ts + id so that
    # _build_symbol receives fills in strict time order within each contract.
    rows = conn.execute(
        select(fills_t)
        .where(fills_t.c.simulated.is_(False))
        .order_by(
            fills_t.c.symbol,
            fills_t.c.option_type,
            fills_t.c.strike,
            fills_t.c.expiry,
            fills_t.c.ts_utc,
            fills_t.c.id,
        )
    ).fetchall()

    # Group fills by full contract identity instead of ticker alone.
    # key: (symbol, option_type, strike, expiry)  value: [row._mapping, ...]
    by_contract: dict[tuple, list] = {}
    fill_pfid: dict[int, str | None] = {}
    for r in rows:
        m = r._mapping
        key = _contract_key(m)
        by_contract.setdefault(key, []).append(m)
        fill_pfid[m["id"]] = m["provider_fill_id"]

    built = 0
    for (symbol, option_type, strike, expiry), contract_rows in by_contract.items():
        label = _build_contract_label(symbol, option_type, strike, expiry)
        for t in _build_symbol(contract_rows):
            row = t["row"]
            row["symbol"] = symbol
            row["contract_label"] = label
            opening_fill_id = _opening_fill_id(t["fill_seq"])
            trade_key = make_trade_key(
                fill_pfid.get(opening_fill_id) if opening_fill_id else None
            )
            trade_id = key_ids.pop(trade_key, None) if trade_key else None
            if trade_id is None:
                trade_id = next_id
                next_id += 1
            row["id"] = trade_id
            row["trade_key"] = trade_key
            conn.execute(trades_t.insert().values(**row))
            for seq, fill_id in enumerate(t["fill_seq"]):
                conn.execute(
                    trade_fills_t.insert().values(
                        trade_id=trade_id, fill_id=fill_id, seq=seq
                    )
                )
            # Re-attach snapshotted enrichment via the stable trade_key.
            if trade_key and trade_key in snapshot:
                _restore_enrichment(conn, trade_id, row, snapshot[trade_key])
            built += 1
    return built


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def import_fills(provider, engine: Engine | None = None, *, full: bool = False) -> dict:
    """Import fills from `provider` and rebuild non-simulated round-trip trades.

    Normally this is **incremental**: we only ask the provider for fills newer
    than the latest ``ts_utc`` already stored (via ``get_fills(since=...)``),
    then merge them into the existing non-simulated fill set. Merging is safe
    because ``_upsert_fills`` dedupes on ``provider_fill_id``, so re-pulling a
    small overlap window (5 minutes of slack around the cursor) never produces
    duplicates. ``_rebuild_trades`` always rebuilds every non-sim trade from
    the full non-sim fill set in the DB, so a merged set still rebuilds
    correctly regardless of how much of it was just re-fetched.

    A **full** refresh — first import into an empty DB, an explicit
    ``full=True``, or a detected mock→live cutover — instead deletes every
    existing non-simulated fill before importing the provider's full history.
    The cutover is self-detecting: mock fills are stored ``simulated=False``
    with ``provider_fill_id`` like ``mock-%``, so if any such row is present
    the wipe is forced even when the caller passes no ``full`` param (the
    frontend auto-import does exactly this on the first live load). This
    ensures demo data can never masquerade as real trades once live import
    starts running.

    Simulated (replay) fills and trades are never touched.

    Returns `{"fills_imported": int, "trades_built": int}`.
    """
    engine = engine or get_engine()
    init_db(engine)

    # Phase 1 (fast, read-only): decide the sync mode and cursor. A full re-walk
    # is forced when `full=True`, when the DB is empty (`_latest_fill_ts` ->
    # None), OR when the existing non-sim rows are mock-origin — that last case
    # is the mock->live cutover, where the table is non-empty but its contents
    # are stale demo data that must be wiped even though the frontend
    # auto-import passes no `full` param.
    with engine.connect() as conn:
        force_full = full or _has_mock_fills(conn)
        since = None if force_full else _latest_fill_ts(conn)
    if since is not None:
        since = since - timedelta(minutes=5)  # overlap guard; dedupe is free

    # Phase 2 (slow, NO transaction held): pull fills from the provider. In live
    # (webull) mode this is a multi-minute rate-gated walk. Doing it outside any
    # write transaction is essential — otherwise the SQLite write lock is held
    # for the whole walk and the Journal's own reads (trades/analytics) stall
    # behind it, hanging the page while a live sync is in flight.
    provider_fills = provider.get_fills(since=since)
    incoming_mock = any(
        (f.provider_fill_id or "").startswith("mock-") for f in provider_fills
    )

    # Phase 3 (fast, write): wipe (only on a full import) + upsert + rebuild.
    # The wipe preserves the cutover guard that clears stale simulated=False
    # mock fills; an incremental refresh instead merges via the idempotent
    # _upsert_fills (dedupe on provider_fill_id).
    with engine.begin() as conn:
        # Snapshot enrichment BEFORE any fill wipe — on a full refresh the wipe
        # deletes the old fills, which would sever the trade->opening-fill join
        # that _snapshot_enrichment relies on for the stable trade_key. Taking it
        # here (pre-wipe) keeps the key derivable; the same provider_fill_id comes
        # back on re-import, so restore re-matches it. (issue #66)
        if incoming_mock and _has_real_fills(conn):
            # Demo fills must never land next to real ones (e.g. the Journal's
            # Import button in mock mode after a Robinhood export import).
            raise MixedFillSourcesError(
                "refusing to import demo (mock) fills into a database that holds "
                "real fills; point TRADEDESK_DB_PATH at a separate demo database"
            )
        snapshot = _snapshot_enrichment(conn)
        key_ids = _snapshot_trade_ids(conn)
        if force_full:
            conn.execute(
                fills_t.delete()
                .where(fills_t.c.simulated.is_(False))
                .where(_not_export_fill())
            )
        fills_imported = _upsert_fills(conn, provider_fills)
        trades_built = _rebuild_trades(conn, snapshot, key_ids)

    return {"fills_imported": fills_imported, "trades_built": trades_built}
