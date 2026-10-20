"""Second-level bars from tick trades (pure helpers, no I/O).

A trade print ``{t, p, s}`` lands in the bar of the second it printed in. Seconds
with no prints produce NO bar (never a flat filler bar), so a quiet contract shows
gaps exactly as a live chart would. 1-second bars are the stored form; coarser
bars (5s) are re-bucketed from them, which gives the same OHLCV as aggregating
the ticks directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.models.market import Candle

SUPPORTED_RES = {"1s": 1, "5s": 5}

_FRACTION = re.compile(r"(\.\d{6})\d+")


class SecondsUnavailable(Exception):
    """The source has no trade prints for this request. ``str(exc)`` is the reason."""


def parse_res(res: str) -> int:
    try:
        return SUPPORTED_RES[res]
    except KeyError:
        raise ValueError(f"unsupported resolution {res!r}; expected one of {sorted(SUPPORTED_RES)}") from None


def parse_trade_ts(s: str) -> datetime:
    """RFC3339 with up to nanosecond digits -> aware UTC datetime (extra digits dropped)."""
    s = _FRACTION.sub(r"\1", s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Aware UTC; a naive datetime is taken as UTC (the DB stores naive UTC)."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Tick:
    ts: datetime  # aware UTC
    price: float
    size: float


def ticks_from_alpaca(rows: Iterable[dict]) -> list[Tick]:
    """Alpaca trade rows ({t, p, s, ...}) -> ticks. Rows without a positive price are dropped."""
    out: list[Tick] = []
    for r in rows:
        try:
            p, s = float(r["p"]), float(r.get("s") or 0)
            ts = parse_trade_ts(r["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if p > 0:
            out.append(Tick(ts=ts, price=p, size=max(s, 0.0)))
    return out


def _bucket(ts: datetime, res_s: int) -> datetime:
    epoch = int(as_utc(ts).timestamp())
    return datetime.fromtimestamp(epoch - epoch % res_s, tz=timezone.utc)


def aggregate_ticks(ticks: Iterable[Tick], symbol: str, res_s: int = 1,
                    source_label: str | None = None) -> list[Candle]:
    """OHLCV bars of ``res_s`` seconds. Input order (by time) decides open/close."""
    ordered = sorted(ticks, key=lambda t: t.ts)  # stable: same-time prints keep arrival order
    bars: dict[datetime, list[float]] = {}
    for t in ordered:
        k = _bucket(t.ts, res_s)
        b = bars.get(k)
        if b is None:
            bars[k] = [t.price, t.price, t.price, t.price, t.size]
        else:
            b[1] = max(b[1], t.price)
            b[2] = min(b[2], t.price)
            b[3] = t.price
            b[4] += t.size
    tf = f"{res_s}s"
    return [
        Candle(symbol=symbol, ts=k, open=v[0], high=v[1], low=v[2], close=v[3],
               volume=v[4], timeframe=tf, source_label=source_label)
        for k, v in sorted(bars.items())
    ]


def rebucket(bars: Iterable[Candle], res_s: int) -> list[Candle]:
    """Coarser bars from 1s bars (open = first, close = last, high/low = extremes)."""
    out: dict[datetime, Candle] = {}
    for b in sorted(bars, key=lambda c: as_utc(c.ts)):
        k = _bucket(b.ts, res_s)
        cur = out.get(k)
        if cur is None:
            out[k] = b.model_copy(update={"ts": k, "timeframe": f"{res_s}s"})
        else:
            out[k] = cur.model_copy(update={
                "high": max(cur.high, b.high), "low": min(cur.low, b.low),
                "close": b.close, "volume": cur.volume + b.volume,
            })
    return [out[k] for k in sorted(out)]


def revealed(bars: Iterable[Candle], playhead: datetime, res_s: int = 1) -> list[Candle]:
    """The server-side no-future gate: bars whose LAST covered second is at or before the
    playhead. A bar stamped S of ``res_s`` seconds covers S .. S+res_s-1, so a 5s bar is
    withheld until the playhead has reached its final second. The playhead is a whole
    second: it means "second P has printed" (the same convention the fills gate uses)."""
    head = int(as_utc(playhead).timestamp())
    return [b for b in bars if int(as_utc(b.ts).timestamp()) + res_s - 1 <= head]
