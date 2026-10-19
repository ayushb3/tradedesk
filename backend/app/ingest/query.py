"""Trade read/enrichment queries (M3).

Backs the journal endpoints:
    list_trades(from_, to, symbol, tag, simulated) -> list[Trade]
    get_trade(trade_id) -> Trade | None
    update_trade(trade_id, patch: TradeUpdate) -> Trade | None

Every returned `Trade` is fully hydrated: its `tags` (joined via
`trade_tags`/`tags`) and its `enrichment` (notes/grade/checklist/screenshots
from `trade_enrichment`) are attached. `update_trade` writes enrichment,
replaces the tag set, and — when a `stop_price` is supplied — recomputes the
`r_multiple` from the trade's avg entry/exit and stop.

M6 context filter (task 6.0.2): `list_trades` excludes context='training' rows
by default so training-drill trades are invisible to journal analytics. The
`simulated` flag (pre-M6 sim trades) is unaffected — simulated trades have
context='real' on old rows (default) and context='sim' on new rows. Either way
they are NOT excluded. Only context='training' is filtered out.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import fills as fills_t
from app.db.schema import tags as tags_t
from app.db.schema import trade_enrichment as enrich_t
from app.db.schema import trade_fills as trade_fills_t
from app.db.schema import trade_tags as trade_tags_t
from app.db.schema import trades as trades_t
from app.models.market import OptionContract
from app.models.review import ReviewFill
from app.models.trades import (
    Direction,
    Grade,
    Side,
    Tag,
    Trade,
    TradeEnrichment,
    TradeStatus,
    TradeUpdate,
)


def _fill_role(side: Side, direction: Direction) -> str:
    """Derive entry/exit role from a fill's side vs. the trade's direction.

    For a long trade a buy opens/increases the position (entry) and a sell
    reduces/closes it (exit); for a short trade the sides swap. M7a data is
    FIFO round-trips, so this mapping is unambiguous (CONTRACT §2.1).
    """
    if direction == Direction.long:
        return "entry" if side == Side.buy else "exit"
    return "entry" if side == Side.sell else "exit"


def get_trade_fills(
    trade_id: int, engine: Engine | None = None
) -> list[ReviewFill]:
    """The executions belonging to a trade, ordered by `trade_fills.seq`.

    Joins `trade_fills` → `fills` and maps each row to a `ReviewFill` with an
    `entry`/`exit` role derived from the fill side against the trade direction
    (CONTRACT §2.1). Returns `[]` when the trade has no fills or doesn't exist.
    """
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        trade_row = conn.execute(
            select(trades_t.c.direction).where(trades_t.c.id == trade_id)
        ).first()
        if trade_row is None:
            return []
        direction = Direction(trade_row._mapping["direction"])

        rows = conn.execute(
            select(
                fills_t.c.id,
                trade_fills_t.c.seq,
                fills_t.c.side,
                fills_t.c.qty,
                fills_t.c.price,
                fills_t.c.ts_utc,
                fills_t.c.fees,
            )
            .select_from(
                trade_fills_t.join(
                    fills_t, trade_fills_t.c.fill_id == fills_t.c.id
                )
            )
            .where(trade_fills_t.c.trade_id == trade_id)
            .order_by(trade_fills_t.c.seq)
        ).fetchall()

    review_fills: list[ReviewFill] = []
    for row in rows:
        m = row._mapping
        side = Side(m["side"])
        review_fills.append(
            ReviewFill(
                id=m["id"],
                seq=m["seq"],
                side=side,
                qty=m["qty"],
                price=m["price"],
                ts=m["ts_utc"],
                fees=m["fees"] or 0.0,
                role=_fill_role(side, direction),
            )
        )
    return review_fills


def get_trade_option_contract(
    trade_id: int, engine: Engine | None = None
) -> OptionContract | None:
    """The option contract a trade traded, or ``None`` for a share trade (M7b).

    All fills in a trade share one contract (the FIFO builder groups by
    ``(symbol, option_type, strike, expiry)``), so we read the option identity
    from any one participating fill. ``option_type is None`` => a stock trade.
    """
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        row = conn.execute(
            select(
                fills_t.c.symbol,
                fills_t.c.option_type,
                fills_t.c.strike,
                fills_t.c.expiry,
            )
            .select_from(
                trade_fills_t.join(fills_t, trade_fills_t.c.fill_id == fills_t.c.id)
            )
            .where(trade_fills_t.c.trade_id == trade_id)
            .order_by(trade_fills_t.c.seq)
        ).first()
    if row is None:
        return None
    m = row._mapping
    if m["option_type"] is None or m["strike"] is None or m["expiry"] is None:
        return None
    return OptionContract(
        underlying=m["symbol"],
        option_type=m["option_type"],
        strike=m["strike"],
        expiry=m["expiry"],
    )


# --------------------------------------------------------------------------- #
# Hydration
# --------------------------------------------------------------------------- #
def _load_tags(conn, trade_id: int) -> list[Tag]:
    rows = conn.execute(
        select(tags_t)
        .select_from(trade_tags_t.join(tags_t, trade_tags_t.c.tag_id == tags_t.c.id))
        .where(trade_tags_t.c.trade_id == trade_id)
        .order_by(tags_t.c.name)
    ).fetchall()
    return [
        Tag(
            id=m["id"],
            name=m["name"],
            description=m["description"],
            active=bool(m["active"]),
        )
        for m in (r._mapping for r in rows)
    ]


def _load_enrichment(conn, trade_id: int) -> TradeEnrichment:
    row = conn.execute(
        select(enrich_t).where(enrich_t.c.trade_id == trade_id)
    ).first()
    if row is None:
        return TradeEnrichment()
    m = row._mapping
    return TradeEnrichment(
        notes=m["notes"],
        grade=Grade(m["grade"]) if m["grade"] else None,
        checklist=m["checklist_json"] or [],
        screenshots=m["screenshots_json"] or [],
        call_source=m["call_source"] if m["call_source"] in ("mentor", "own") else None,
    )


def _hydrate(conn, row) -> Trade:
    m = row._mapping
    trade_id = m["id"]
    return Trade(
        id=trade_id,
        trade_key=m.get("trade_key"),
        symbol=m["symbol"],
        contract_label=m.get("contract_label"),
        direction=Direction(m["direction"]),
        open_ts=m["open_ts_utc"],
        close_ts=m["close_ts_utc"],
        session_date=m["session_date"],
        qty=m["qty"],
        avg_entry=m["avg_entry"],
        avg_exit=m["avg_exit"],
        gross_pnl=m["gross_pnl"],
        fees=m["fees"],
        net_pnl=m["net_pnl"],
        pnl_pct=m["pnl_pct"],
        stop_price=m["stop_price"],
        r_multiple=m["r_multiple"],
        hold_time_s=m["hold_time_s"],
        status=TradeStatus(m["status"]),
        simulated=bool(m["simulated"]),
        tags=_load_tags(conn, trade_id),
        enrichment=_load_enrichment(conn, trade_id),
    )


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
def apply_rule_results(trades: list[Trade], engine: Engine) -> list[Trade]:
    """Fill ``r_multiple`` / ``r_basis`` / ``rule_results`` on real trades from the desk
    ledger, so every trade read uses the one R definition. Sim trades are left alone.
    If the rules file cannot be read the stored values are returned unchanged."""
    real = [t for t in trades if not t.simulated and t.id is not None]
    if not real:
        return trades
    try:
        from app.desk import days as desk_days

        found = desk_days.analysis_by_trade(
            min(t.session_date for t in real), max(t.session_date for t in real), engine=engine
        )
    except ValueError:
        return trades
    out = []
    for t in trades:
        f = found.get(t.id) if not t.simulated else None
        out.append(t if f is None else t.model_copy(update={
            "r_multiple": f.r, "r_basis": f.r_basis, "rule_results": f.rule_results,
        }))
    return out


def list_trades(
    from_: date | None = None,
    to: date | None = None,
    symbol: str | None = None,
    tag: str | None = None,
    simulated: bool | None = None,
    engine: Engine | None = None,
) -> list[Trade]:
    """Filtered, hydrated trade list ordered by open_ts descending.

    M6 context filter: context='training' trades are always excluded from
    journal analytics. This default cannot be overridden from here — callers
    that explicitly need training trades should query directly via training_store.
    Sim trades (simulated=True or context='sim') are NOT excluded.
    """
    engine = engine or get_engine()
    init_db(engine)

    stmt = select(trades_t)
    if from_ is not None:
        stmt = stmt.where(trades_t.c.session_date >= from_)
    if to is not None:
        stmt = stmt.where(trades_t.c.session_date <= to)
    if symbol is not None:
        stmt = stmt.where(trades_t.c.symbol == symbol)
    if simulated is not None:
        stmt = stmt.where(trades_t.c.simulated.is_(simulated))
    if tag is not None:
        stmt = stmt.where(
            trades_t.c.id.in_(
                select(trade_tags_t.c.trade_id)
                .select_from(
                    trade_tags_t.join(tags_t, trade_tags_t.c.tag_id == tags_t.c.id)
                )
                .where(tags_t.c.name == tag)
            )
        )
    # M6: always exclude training-context trades from the journal analytics read.
    # The `context` column was added via ALTER TABLE in connection.py; existing
    # rows default to 'real', so this filter is safe on pre-M6 DBs too.
    stmt = stmt.where(trades_t.c.context != "training")
    stmt = stmt.order_by(trades_t.c.open_ts_utc.desc())

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()
        trades = [_hydrate(conn, r) for r in rows]
    return apply_rule_results(trades, engine)


def get_trade(trade_id: int, engine: Engine | None = None) -> Trade | None:
    """A single hydrated trade, or None if it doesn't exist."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        row = conn.execute(
            select(trades_t).where(trades_t.c.id == trade_id)
        ).first()
        if row is None:
            return None
        trade = _hydrate(conn, row)
    return apply_rule_results([trade], engine)[0]


# --------------------------------------------------------------------------- #
# Update / enrichment
# --------------------------------------------------------------------------- #
def _recompute_r_multiple(m, stop_price: float) -> float | None:
    """R-multiple from avg entry/exit and stop. None if not computable."""
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


def _upsert_enrichment(conn, trade_id: int, patch: TradeUpdate) -> None:
    values: dict = {}
    if patch.notes is not None:
        values["notes"] = patch.notes
    if patch.grade is not None:
        values["grade"] = patch.grade.value
    if patch.checklist is not None:
        values["checklist_json"] = patch.checklist
    if patch.screenshots is not None:
        values["screenshots_json"] = patch.screenshots
    if "call_source" in patch.model_fields_set:  # explicit null clears
        values["call_source"] = patch.call_source
    if not values:
        return

    exists = conn.execute(
        select(enrich_t.c.trade_id).where(enrich_t.c.trade_id == trade_id)
    ).first()
    if exists is None:
        conn.execute(enrich_t.insert().values(trade_id=trade_id, **values))
    else:
        conn.execute(
            enrich_t.update().where(enrich_t.c.trade_id == trade_id).values(**values)
        )


def update_trade(
    trade_id: int, patch: TradeUpdate, engine: Engine | None = None
) -> Trade | None:
    """Apply enrichment / tags / stop to a trade and return it re-hydrated."""
    engine = engine or get_engine()
    init_db(engine)

    with engine.begin() as conn:
        row = conn.execute(
            select(trades_t).where(trades_t.c.id == trade_id)
        ).first()
        if row is None:
            return None
        m = row._mapping

        _upsert_enrichment(conn, trade_id, patch)

        if patch.tag_ids is not None:
            conn.execute(
                trade_tags_t.delete().where(trade_tags_t.c.trade_id == trade_id)
            )
            for tag_id in patch.tag_ids:
                conn.execute(
                    trade_tags_t.insert().values(trade_id=trade_id, tag_id=tag_id)
                )

        if patch.stop_price is not None:
            values = {"stop_price": patch.stop_price}
            if m["simulated"]:  # only sim trades store a stop-based R; real R is derived
                values["r_multiple"] = _recompute_r_multiple(m, patch.stop_price)
            conn.execute(trades_t.update().where(trades_t.c.id == trade_id).values(**values))

    return get_trade(trade_id, engine=engine)
