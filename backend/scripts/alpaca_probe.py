#!/usr/bin/env python
"""Alpaca market-data probe (issue #48). Read-only; never prints keys.

    python backend/scripts/alpaca_probe.py --stock SPY --day 2026-06-19 \
        --option SPY260619C00600000 --option SPXW260619C06000000

For the stock and each option contract prints: HTTP status, bar count, first/last
bar time (PT), feed used, and the request's date range. Exit codes: 0 always for
data results (a 403/404 is a finding, not a failure); 2 when keys are missing.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def load_dotenv_simple(path: Path) -> None:
    """Minimal KEY=VALUE parser; real environment wins. Never echoes values."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _pt(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(PT)
    return dt.strftime("%Y-%m-%d %H:%M PT")


def _summarize(label: str, pages: list[dict], extract, feed: str | None, status: int,
               start: str, end: str) -> None:
    bars = [b for p in pages for b in extract(p)]
    first = _pt(bars[0]["t"]) if bars else "-"
    last = _pt(bars[-1]["t"]) if bars else "-"
    print(f"{label}: HTTP {status} bars={len(bars)} first={first} last={last} "
          f"feed={feed or 'n/a'} range={start}..{end}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stock", default="SPY")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (a past trading day)")
    ap.add_argument("--option", action="append", default=[], help="OCC symbol; repeatable")
    ap.add_argument("--feed", default=None, help="iex|sip (default TRADEDESK_ALPACA_FEED or sip)")
    args = ap.parse_args(argv)

    load_dotenv_simple(ROOT / ".env")

    from app.providers.alpaca.client import AlpacaDataClient, load_keys
    from app.providers.alpaca.errors import AlpacaConfigError, AlpacaHTTPError
    from app.providers.alpaca.symbols import rfc3339

    try:
        load_keys()
    except AlpacaConfigError:
        print("Missing APCA_API_KEY_ID / APCA_API_SECRET_KEY (env or repo .env).", file=sys.stderr)
        return 2

    day = date.fromisoformat(args.day)
    start = datetime.combine(day, time(4, 0), ET).astimezone(UTC)
    end = datetime.combine(day, time(20, 0), ET).astimezone(UTC)
    s, e = rfc3339(start), rfc3339(end)
    client = AlpacaDataClient()
    feed = (args.feed or os.environ.get("TRADEDESK_ALPACA_FEED") or "sip").lower()

    # Stock bars (with the same 403 -> iex fallback the provider uses).
    params = {"timeframe": "1Min", "start": s, "end": e, "limit": 10000,
              "adjustment": "raw", "feed": feed}
    path = f"/v2/stocks/{args.stock.upper()}/bars"
    label = f"stock {args.stock.upper()} {args.day}"
    for attempt_feed in ([feed, "iex"] if feed != "iex" else ["iex"]):
        params["feed"] = attempt_feed
        try:
            pages = list(client.pages(path, params))
            _summarize(label, pages, lambda p: p.get("bars") or [], attempt_feed, 200, s, e)
            break
        except AlpacaHTTPError as exc:
            print(f"{label}: HTTP {exc.status} feed={attempt_feed} range={s}..{e}")
            if exc.status != 403:
                break

    # Option bars (indicative/OPRA feed is chosen by the plan, not a parameter).
    for occ in args.option:
        oparams = {"symbols": occ, "timeframe": "1Min", "start": s, "end": e, "limit": 10000}
        label = f"option {occ}"
        try:
            pages = list(client.pages("/v1beta1/options/bars", oparams))
            _summarize(label, pages, lambda p, o=occ: (p.get("bars") or {}).get(o, []),
                       "plan-default", 200, s, e)
        except AlpacaHTTPError as exc:
            print(f"{label}: HTTP {exc.status} bars=0 range={s}..{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
