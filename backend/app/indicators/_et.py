"""Shared ET wall-clock helpers for the indicators/levels engine.

Candle timestamps are UTC bar-open datetimes. Session classification (premarket,
regular, opening-range) is defined in **Eastern** wall-clock, so every level
builder converts each bar's ``ts`` to ``America/New_York`` via :mod:`zoneinfo`
(DST-correct) and reduces it to minutes-since-ET-midnight.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

# ET session boundaries as minutes since ET midnight.
PREMARKET_OPEN_MIN = 4 * 60        # 04:00 ET
REG_OPEN_MIN = 9 * 60 + 30         # 09:30 ET
REG_CLOSE_MIN = 16 * 60            # 16:00 ET
AFTERHOURS_CLOSE_MIN = 20 * 60     # 20:00 ET


@lru_cache(maxsize=8)
def _zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def et_minute_of_day(ts: datetime, tz: str = "America/New_York") -> int:
    """Return ``ts``'s minutes-since-midnight in ET (DST-correct).

    ``ts`` is expected tz-aware UTC; a naive datetime is assumed UTC.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    et = ts.astimezone(_zone(tz))
    return et.hour * 60 + et.minute


def is_premarket(etmin: int) -> bool:
    return PREMARKET_OPEN_MIN <= etmin < REG_OPEN_MIN


def is_regular(etmin: int) -> bool:
    return REG_OPEN_MIN <= etmin < REG_CLOSE_MIN
