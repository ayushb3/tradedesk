"""UTC datetime type for API models.

The DB stores naive UTC (decision #5). Serialized as-is, a naive value goes out
as "2026-10-14T14:15:23" with no offset, and the browser's `new Date(...)`
reads that as *local* time: a Pacific browser shifts every fill 7h late.
`UtcDatetime` keeps the Python value unchanged and only changes the JSON form:
naive is taken as UTC, aware is converted to UTC, and the string ends in "Z".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import PlainSerializer


def utc_iso(value: datetime) -> str:
    """ISO-8601 UTC with a trailing 'Z' (naive input is assumed UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(utc_iso, return_type=str, when_used="json")]
