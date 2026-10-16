"""Plan cards (issue #9): contract parsing, store, and the late-plan helper.

Contract parsing lives here so the chat and the page share it. Matching a plan to a
trade is issue #10; `is_late` / `mark_late` are the pieces it will call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import plans as plans_t
from app.desk.rules import TZ_NAME
from app.models.plans import Plan

PT = ZoneInfo(TZ_NAME)

_CONTRACT_RE = re.compile(
    r"^\s*(?P<sym>[A-Za-z][A-Za-z.]{0,5})\s+(?P<strike>\d+(?:\.\d+)?)\s*(?P<cp>[CcPp])"
    r"(?:\s+(?P<exp>\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2}))?\s*$"
)


@dataclass(frozen=True)
class Contract:
    symbol: str
    option_type: str  # CALL | PUT
    strike: float
    expiry: date_type


def today_pt() -> date_type:
    return datetime.now(timezone.utc).astimezone(PT).date()


def _parse_expiry(text: str, session_date: date_type) -> date_type:
    if "-" in text:
        return date_type.fromisoformat(text)
    parts = [int(p) for p in text.split("/")]
    month, day = parts[0], parts[1]
    if len(parts) == 3:
        year = parts[2] + 2000 if parts[2] < 100 else parts[2]
        return date_type(year, month, day)
    exp = date_type(session_date.year, month, day)
    if exp < session_date:  # "1/2" typed in December means next year
        exp = date_type(session_date.year + 1, month, day)
    return exp


def parse_contract(text: str, session_date: date_type) -> Contract:
    """'SPY 580P' -> SPY / PUT / 580.0, expiring `session_date` (0DTE) unless given."""
    m = _CONTRACT_RE.match(text or "")
    if not m:
        raise ValueError(
            f"Cannot parse contract {text!r}; expected e.g. 'SPY 580P' or 'SPY 580P 10/17'"
        )
    try:
        expiry = _parse_expiry(m["exp"], session_date) if m["exp"] else session_date
    except ValueError as exc:
        raise ValueError(f"Bad expiry in contract {text!r}") from exc
    strike = float(m["strike"])
    if strike <= 0:
        raise ValueError("strike must be positive")
    return Contract(
        symbol=m["sym"].upper(),
        option_type="CALL" if m["cp"].upper() == "C" else "PUT",
        strike=strike,
        expiry=expiry,
    )


def _naive_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


def is_late(plan: Plan, opening_fill_ts: datetime) -> bool:
    """A plan written after the opening fill of its trade is late: it does not count as
    'size written first'. Naive datetimes are taken as UTC."""
    return _naive_utc(plan.created_at) > _naive_utc(opening_fill_ts)


def pt_label(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(PT).strftime("%H:%M") + " PT"


def _to_model(row) -> Plan:
    m = row._mapping
    created = m["created_at"].replace(tzinfo=timezone.utc)
    return Plan(
        id=m["id"], created_at=created, created_pt=pt_label(created),
        session_date=m["session_date"], caller=m["caller"], symbol=m["symbol"],
        option_type=m["option_type"], strike=m["strike"], expiry=m["expiry"],
        size_usd=m["size_usd"], max_loss_usd=m["max_loss_usd"], target=m["target"],
        note=m["note"], status=m["status"], late=bool(m["late"]),
        matched_trade_id=m["matched_trade_id"],
    )


def add_plan(
    *,
    caller: str,
    contract: str,
    size_usd: float,
    max_loss_usd: float,
    target: str | None = None,
    note: str = "",
    session_date: date_type | None = None,
    created_at: datetime | None = None,
    engine: Engine | None = None,
) -> Plan:
    if not isinstance(caller, str) or not caller.strip():
        raise ValueError("caller is required")
    for name, v in (("size_usd", size_usd), ("max_loss_usd", max_loss_usd)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{name} must be a number")
    if size_usd <= 0:
        raise ValueError("size_usd must be greater than 0")
    if max_loss_usd <= 0:
        raise ValueError("max_loss_usd must be greater than 0")
    if max_loss_usd > size_usd:
        raise ValueError("max_loss_usd cannot exceed size_usd")

    created = created_at or datetime.now(timezone.utc)
    if session_date is None:
        aware = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        session_date = aware.astimezone(PT).date()
    parsed = parse_contract(contract, session_date)

    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        res = conn.execute(
            plans_t.insert().values(
                created_at=_naive_utc(created), session_date=session_date.isoformat(),
                caller=caller.strip(), symbol=parsed.symbol,
                option_type=parsed.option_type, strike=parsed.strike,
                expiry=parsed.expiry.isoformat(), size_usd=float(size_usd),
                max_loss_usd=float(max_loss_usd), target=target, note=note or "",
                status="open", late=False, matched_trade_id=None,
            )
        )
        pid = res.inserted_primary_key[0]
        row = conn.execute(select(plans_t).where(plans_t.c.id == pid)).one()
    return _to_model(row)


def get_plan(plan_id: int, engine: Engine | None = None) -> Plan | None:
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        row = conn.execute(select(plans_t).where(plans_t.c.id == plan_id)).first()
    return _to_model(row) if row else None


def list_plans(date: date_type | str, engine: Engine | None = None) -> list[Plan]:
    key = date if isinstance(date, date_type) else date_type.fromisoformat(date)
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            select(plans_t)
            .where(plans_t.c.session_date == key.isoformat())
            .order_by(plans_t.c.created_at, plans_t.c.id)
        ).all()
    return [_to_model(r) for r in rows]


def mark_late(plan_id: int, opening_fill_ts: datetime, engine: Engine | None = None) -> Plan:
    """Persist `late` for a plan against its trade's opening fill (used by matching, #10)."""
    plan = get_plan(plan_id, engine)
    if plan is None:
        raise ValueError(f"No plan with id {plan_id}")
    late = is_late(plan, opening_fill_ts)
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(plans_t.update().where(plans_t.c.id == plan_id).values(late=late))
    result = get_plan(plan_id, engine)
    assert result is not None
    return result
