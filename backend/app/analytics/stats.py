"""Journal analytics stat functions (M3) — quant-owned.

The API layer (`app.api.routes.analytics`) calls the four public functions here:
`summary`, `calendar`, `by_dimension`, `daily_review`. All read trades through
`app.ingest.query.list_trades` (the canonical READ source owned by data-eng).
Until that module lands we transparently fall back to the M2 sim-persistence
reader (`app.ingest.sim.list_sim_trades`) so analytics works off replay trades
without depending on data-eng's import timing.

The actual number-crunching lives in small pure helpers (`compute_summary`,
`_group_stats`, `_hour_bucket`, …) that take plain `Trade` lists so QA can
unit-test them on hand-built fixtures with no database.

Definitions (frozen contract):
- win  = net_pnl > 0 ; loss = net_pnl < 0 ; breakeven (net_pnl == 0) is neither.
- gross_profit = sum(net_pnl of wins) ; gross_loss = sum(net_pnl of losses) (<= 0).
- profit_factor = gross_profit / abs(gross_loss); None when there are no losses
  (inf-guard) or no trades.
- expectancy = win_rate * avg_win - (1 - win_rate) * abs(avg_loss).
- avg_r is the mean r_multiple over trades that have one (r_multiple is not None).
Money is rounded to 2dp, rates/ratios to 4dp.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.models.trades import Trade, TradeStatus

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Read seam                                                                    #
# --------------------------------------------------------------------------- #
def _load_trades(
    from_: date | None = None,
    to: date | None = None,
    symbol: str | None = None,
    tag: str | None = None,
    simulated: bool | None = None,
) -> list[Trade]:
    """Canonical read: `app.ingest.query.list_trades`, with a sim fallback.

    data-eng owns `list_trades`; if it is not importable yet we read the
    replay trades persisted by M2 (`list_sim_trades`) and apply the same
    filters in memory so the analytics surface still functions.

    M6 context filter: training trades (context='training') are excluded by
    default. list_trades handles this; the fallback path filters in memory.
    Sim trades (context='sim' / simulated=True) are NOT excluded — they are
    valid journal entries. Only 'training' drill trades are hidden.
    """
    try:
        from app.ingest.query import list_trades as _list
    except ImportError:
        return _load_trades_fallback(
            from_=from_, to=to, symbol=symbol, tag=tag, simulated=simulated
        )
    return _list(from_=from_, to=to, symbol=symbol, tag=tag, simulated=simulated)


def _load_trades_fallback(
    from_: date | None = None,
    to: date | None = None,
    symbol: str | None = None,
    tag: str | None = None,
    simulated: bool | None = None,
) -> list[Trade]:
    """Sim-only read path used until `app.ingest.query` exists."""
    if simulated is False:
        # No live-trade source available without the canonical reader.
        return []
    from app.ingest.sim import list_sim_trades

    trades = list_sim_trades()
    out = []
    for t in trades:
        if symbol is not None and t.symbol != symbol:
            continue
        if from_ is not None and t.session_date < from_:
            continue
        if to is not None and t.session_date > to:
            continue
        if tag is not None and tag not in {tg.name for tg in t.tags}:
            continue
        out.append(t)
    return out


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #
def _closed(trades: list[Trade]) -> list[Trade]:
    """Closed trades that carry a realized net_pnl."""
    return [
        t
        for t in trades
        if t.status == TradeStatus.closed and t.net_pnl is not None
    ]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _money(x: float | None) -> float | None:
    return round(x, 2) if x is not None else None


def _rate(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


def _empty_summary() -> dict:
    return {
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "total_pnl": 0.0,
        "profit_factor": None,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "avg_r": None,
        "expectancy": 0.0,
        "avg_hold_time_s": None,
        "best_trade": None,
        "worst_trade": None,
    }


def compute_summary(trades: list[Trade]) -> dict:
    """Full summary stats over an already-loaded trade list (pure)."""
    closed = _closed(trades)
    n = len(closed)
    if n == 0:
        return _empty_summary()

    pnls = [t.net_pnl for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_count = len(wins)
    loss_count = len(losses)
    gross_profit = sum(wins)
    gross_loss = sum(losses)  # <= 0
    total_pnl = sum(pnls)

    win_rate = win_count / n
    avg_win = gross_profit / win_count if win_count else 0.0
    avg_loss = gross_loss / loss_count if loss_count else 0.0  # <= 0
    profit_factor = gross_profit / abs(gross_loss) if gross_loss != 0 else None
    expectancy = win_rate * avg_win - (1 - win_rate) * abs(avg_loss)

    r_vals = [t.r_multiple for t in closed if t.r_multiple is not None]
    hold_vals = [t.hold_time_s for t in closed if t.hold_time_s is not None]
    avg_hold = _mean(hold_vals)

    return {
        "trade_count": n,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": _rate(win_rate),
        "gross_profit": _money(gross_profit),
        "gross_loss": _money(gross_loss),
        "total_pnl": _money(total_pnl),
        "profit_factor": _rate(profit_factor),
        "avg_win": _money(avg_win),
        "avg_loss": _money(avg_loss),
        "avg_r": _rate(_mean(r_vals)),
        "expectancy": _money(expectancy),
        "avg_hold_time_s": round(avg_hold, 1) if avg_hold is not None else None,
        "best_trade": _money(max(pnls)),
        "worst_trade": _money(min(pnls)),
    }


def _group_stats(key: str, trades: list[Trade]) -> dict:
    """Per-group stat row for `by_dimension` (pure, over closed trades)."""
    n = len(trades)
    pnl = sum(t.net_pnl for t in trades)
    win_count = sum(1 for t in trades if t.net_pnl > 0)
    r_vals = [t.r_multiple for t in trades if t.r_multiple is not None]
    return {
        "key": key,
        "trade_count": n,
        "pnl": _money(pnl),
        "win_rate": _rate(win_count / n) if n else 0.0,
        "avg_r": _rate(_mean(r_vals)),
    }


def _hour_bucket(open_ts: datetime, tz: str) -> str:
    """Hour-of-day bucket (e.g. '06:00') in zone `tz` for a naive-UTC timestamp."""
    dt = open_ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(ZoneInfo(tz))
    return f"{local.hour:02d}:00"


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #
def summary(
    from_: date | None = None,
    to: date | None = None,
    symbol: str | None = None,
    simulated: bool | None = None,
) -> dict:
    """Aggregate performance stats over the filtered closed trades."""
    trades = _load_trades(from_=from_, to=to, symbol=symbol, simulated=simulated)
    return compute_summary(trades)


def calendar(month: str) -> dict:
    """Per-day P&L map for a calendar month ('YYYY-MM').

    Returns {"YYYY-MM-DD": {"pnl", "trades", "wins", "losses"}} for each
    **close date** in the month that has closed trades.

    Important: each trade is attributed to its *exit/close* date (ET) rather
    than its session_date (open date) — this matches how Webull's P&L calendar
    buckets realized P&L, and is the correct behaviour when a trade opens on one
    day and closes on another (e.g. a position opened Jul 8 and closed Jul 9
    appears on Jul 9 in both Webull and here).

    Open trades (no close_ts) have no realized P&L and are excluded.
    """
    year, mon = (int(p) for p in month.split("-", 1))
    if mon == 12:
        last = date(year, 12, 31)
    else:
        from datetime import timedelta

        last = date(year, mon + 1, 1) - timedelta(days=1)
    first = date(year, mon, 1)

    # Load a slightly wider window so trades opened in the prior month but
    # closed inside this month are included.  We re-filter by close_date below.
    trades = _closed(_load_trades())
    out: dict[str, dict] = {}
    for t in trades:
        # Derive the ET close date from close_ts.
        if t.close_ts is None:
            continue  # open trade — no realized P&L
        close_dt = t.close_ts
        # close_ts is stored as naive UTC in the DB; treat it as UTC before
        # converting to ET (mirrors the _session_date() approach in imports.py).
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=_UTC)
        close_date = close_dt.astimezone(_ET).date()
        if close_date < first or close_date > last:
            continue
        key = close_date.isoformat()
        cell = out.setdefault(key, {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})
        cell["pnl"] += t.net_pnl
        cell["trades"] += 1
        if t.net_pnl > 0:
            cell["wins"] += 1
        elif t.net_pnl < 0:
            cell["losses"] += 1
    for cell in out.values():
        cell["pnl"] = _money(cell["pnl"])
    return out


def by_dimension(
    dimension: str,
    from_: date | None = None,
    to: date | None = None,
) -> list[dict]:
    """Group closed trades by a dimension and rank groups by P&L desc.

    dimension in {ticker, tod, tag, setup}:
      - ticker -> symbol
      - tod    -> display-zone (PT by default) hour-of-day bucket of open_ts
      - tag / setup -> each tag name (a trade with N tags hits N groups)
    """
    if dimension not in {"ticker", "tod", "tag", "setup"}:
        raise ValueError(f"unknown dimension: {dimension!r}")

    trades = _closed(_load_trades(from_=from_, to=to))
    groups: dict[str, list[Trade]] = {}

    def _add(key: str, trade: Trade) -> None:
        groups.setdefault(key, []).append(trade)

    from app.config import get_settings

    display_tz = get_settings().display_tz
    for t in trades:
        if dimension == "ticker":
            _add(t.symbol, t)
        elif dimension == "tod":
            _add(_hour_bucket(t.open_ts, display_tz), t)
        else:  # tag | setup — one group per tag name
            for tg in t.tags:
                _add(tg.name, t)

    rows = [_group_stats(key, grp) for key, grp in groups.items()]
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return rows


def _prompts(smry: dict) -> list[str]:
    """3–6 journal prompts, lightly tailored to the day's outcome."""
    prompts = [
        "Did you follow your entry rules on every trade?",
        "How closely did your position sizing match your plan?",
    ]
    if smry["win_count"]:
        prompts.append("Which trade had the best R, and what did you do right?")
    if smry["loss_count"]:
        prompts.append("On your losing trades, was the stop honored or moved?")
    if smry["loss_count"] and smry["win_count"]:
        prompts.append(
            "What separated your winners from your losers today?"
        )
    if smry["trade_count"] == 0:
        prompts.append("No trades today — was sitting out the right call?")
    prompts.append("What is the one adjustment for tomorrow?")
    return prompts[:6]


def daily_review(day: date) -> dict:
    """Everything the daily-review view needs for a single trading day."""
    trades = _load_trades(from_=day, to=day)
    smry = summary(from_=day, to=day)
    return {
        "date": day.isoformat(),
        "trades": [t.model_dump(mode="json") for t in trades],
        "summary": smry,
        "prompts": _prompts(smry),
    }
