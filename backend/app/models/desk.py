"""Desk models (issue #7 onward). Later slices add to this file."""
from __future__ import annotations

from datetime import date as date_type

from pydantic import BaseModel, Field

from .utc import UtcDatetime


class DayFields(BaseModel):
    """One day's self-report. Keyed on ISO date, independent of trades."""

    date: date_type
    why: str = ""
    callers: list[str] = Field(default_factory=list)
    size_written_first: bool | None = None
    max_loss_declared: float | None = None  # dollars
    closed_by_window_end: bool | None = None
    stop_hit: bool | None = None
    target_hit: bool | None = None
    how_felt: str = ""
    notes: str = ""
    updated_at: UtcDatetime
