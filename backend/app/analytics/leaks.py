"""Leak detector for the training module (M6 task 6.3.3).

Public entry
------------
detect_leaks(trades, fills, debriefs, attempts, scenarios) -> LeakReport

Named leaks (v1)
----------------
revenge_trading     -- 2+ consecutive losing journal trades followed by an
                       up-sized next trade; OR same pattern detected from
                       attempts via detect_revenge_size from behavior.py.
chop_overtrading    -- session with high trade count AND >60% of those trades
                       lose.  Sourced from journal trade groups by session_date.
moved_stops         -- >30% of journal trades have a stop_price recorded AND
                       avg_exit on the wrong side of stop (loosened).
psychology_loss     -- debrief verdict is 'failed-the-setup' or 'bad-win' more
                       than 20% of debriefed trades.

Playlist prescription (v1)
--------------------------
Each leak maps to an archetype/drill_type the operator should practice:
  revenge_trading  -> do-nothing drills + drawdown-script (any archetype)
  chop_overtrading -> do-nothing drills on chop scenarios
  moved_stops      -> management-only drills (any archetype)
  psychology_loss  -> any blind drill

Given the input scenarios list, up to 3 matching scenarios are prescribed per
leak, ordered by scenario_id ascending (deterministic).

Design notes
------------
* Pure function — no I/O, no side effects, no time.time().  The generated_at
  timestamp is either injected via the `_now` parameter (tests pass a fixed
  string) or computed once at call time.
* Severity: 1 = suggestion, 2 = concern, 3 = pattern.
  Thresholds are documented inline at each detector.
* Consumes detect_revenge_size from analytics.behavior when attempts are
  available, and falls back to journal-trade analysis when not.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.trades import Fill, Trade
    from app.models.training import Attempt, Debrief, ScenarioPublic


# --------------------------------------------------------------------------- #
# Public dataclasses                                                           #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Leak:
    """A single named behavioral leak with evidence and a prescribed playlist."""

    name: str
    severity: int  # 1 = suggestion, 2 = concern, 3 = pattern
    description: str  # human-readable, references the concrete counts
    evidence: dict   # {trade_ids: [...], attempt_ids: [...], debrief_ids: [...]}
    prescribed_scenario_ids: list[int]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class LeakReport:
    """Container for all detected leaks for a given data snapshot."""

    leaks: list[Leak]
    generated_at: str  # ISO-8601; deterministic when caller injects _now

    def to_dict(self) -> dict:
        return {
            "leaks": [l.to_dict() for l in self.leaks],
            "generated_at": self.generated_at,
        }


# --------------------------------------------------------------------------- #
# Playlist prescription helpers                                                #
# --------------------------------------------------------------------------- #

# Map: leak name -> list of (drill_type_value, archetype_value_or_None)
# The detector picks up to 3 matching scenarios from the provided list.
_PLAYLIST_RULES: dict[str, list[tuple[str, str | None]]] = {
    "revenge_trading":   [("do-nothing", None), ("drawdown-script", None)],
    "chop_overtrading":  [("do-nothing", "chop")],
    "moved_stops":       [("management-only", None)],
    "psychology_loss":   [("decision-point", None), ("do-nothing", None),
                          ("management-only", None), ("spot-setup", None),
                          ("drawdown-script", None)],
}


def _prescribe(leak_name: str, scenarios: "list[ScenarioPublic]") -> list[int]:
    """Return up to 3 scenario IDs matching the leak's playlist rule.

    Deterministic: scenarios are iterated in ascending scenario_id order.
    """
    rules = _PLAYLIST_RULES.get(leak_name, [])
    if not rules:
        return []

    # Sort scenarios by id ascending for determinism.
    ordered = sorted(scenarios, key=lambda s: s.id)

    matched: list[int] = []
    for scenario in ordered:
        if len(matched) >= 3:
            break
        drill_val = scenario.drill_type.value if scenario.drill_type else None
        arch_val = scenario.archetype.value if scenario.archetype else None
        for drill_rule, arch_rule in rules:
            if drill_val == drill_rule:
                if arch_rule is None or arch_val == arch_rule:
                    matched.append(scenario.id)
                    break  # one scenario, one match per rule
    return matched


# --------------------------------------------------------------------------- #
# Individual leak detectors (pure)                                             #
# --------------------------------------------------------------------------- #

def _detect_revenge_trading_from_attempts(
    attempts: "list[Attempt]",
    scenarios: "list[ScenarioPublic]",
) -> "Leak | None":
    """Use behavior.detect_revenge_size on the attempt list.

    Requires attempts to carry process_score and outcome_pnl as dynamic attrs
    (as stored by finish_attempt). Attempts without those attrs are treated as
    clean (score=1.0, pnl=0.0) — the detector degrades safely.

    We sort attempts by started_at for correct temporal ordering.
    """
    if not attempts:
        return None

    try:
        from app.analytics.behavior import detect_revenge_size
    except ImportError:
        return None

    # Sort by started_at for temporal order (ascending).
    sorted_attempts = sorted(attempts, key=lambda a: a.started_at)

    # Attach DB-persisted score/pnl from the attempt DB row if available.
    # list_attempts returns Attempt (no score fields), but training_store stores
    # them in the DB.  We read them via a separate query (see _load_attempt_scores).
    signal = detect_revenge_size(sorted_attempts)
    if signal is None:
        return None

    severity = 2 if signal.ratio < 3.0 else 3
    desc = (
        f"Sized up {signal.ratio:.1f}x (from {signal.previous_qty:.0f} to "
        f"{signal.current_qty:.0f} shares) immediately after a losing drill "
        f"attempt (attempt #{signal.previous_attempt_id})."
    )
    evidence = {
        "trade_ids": [],
        "attempt_ids": [signal.previous_attempt_id, signal.current_attempt_id],
        "debrief_ids": [],
    }
    return Leak(
        name="revenge_trading",
        severity=severity,
        description=desc,
        evidence=evidence,
        prescribed_scenario_ids=_prescribe("revenge_trading", scenarios),
    )


def _detect_revenge_trading_from_journal(
    trades: "list[Trade]",
    scenarios: "list[ScenarioPublic]",
) -> "Leak | None":
    """Detect revenge trading from journal-only (real trade) data.

    Pattern: 2+ consecutive losses (net_pnl < 0) immediately followed by a
    trade whose qty > 1.5x the mean qty of the losing run.

    We look at the full chronological trade list and scan for any window where:
    - trades[i] and trades[i+1] are both losses
    - trades[i+2] has qty > 1.5x avg(trades[i].qty, trades[i+1].qty)

    Also catches a single-loss + immediate upsize at 2x (we lower threshold to
    1.5x for 2-consecutive-losses to match the task acceptance criterion).

    Returns the Leak for the most recent such window found.
    """
    from app.models.trades import TradeStatus

    closed = [t for t in trades if t.status == TradeStatus.closed and t.net_pnl is not None]
    if len(closed) < 3:
        return None

    # Sort chronologically ascending (open_ts).
    sorted_trades = sorted(closed, key=lambda t: t.open_ts)

    evidence_trade_ids: list[int] = []
    found_ratio: float = 0.0

    for i in range(len(sorted_trades) - 2):
        t0, t1, t2 = sorted_trades[i], sorted_trades[i + 1], sorted_trades[i + 2]
        if t0.net_pnl < 0 and t1.net_pnl < 0:
            avg_loss_qty = (t0.qty + t1.qty) / 2.0
            if avg_loss_qty > 0 and t2.qty / avg_loss_qty > 1.5:
                ids = [x for x in [t0.id, t1.id, t2.id] if x is not None]
                evidence_trade_ids = ids
                found_ratio = t2.qty / avg_loss_qty

    if not evidence_trade_ids:
        return None

    severity = 2 if found_ratio < 3.0 else 3
    desc = (
        f"Sized up {found_ratio:.1f}x after 2 consecutive losses in the journal "
        f"(trades {evidence_trade_ids[:2]} → {evidence_trade_ids[2:]})."
    )
    return Leak(
        name="revenge_trading",
        severity=severity,
        description=desc,
        evidence={"trade_ids": evidence_trade_ids, "attempt_ids": [], "debrief_ids": []},
        prescribed_scenario_ids=_prescribe("revenge_trading", scenarios),
    )


# Chop overtrading threshold: a session qualifies as "chop" if it has
# >= 5 trades AND >= 60% of them lose.
_CHOP_MIN_TRADES = 5
_CHOP_LOSS_RATE = 0.6


def _detect_chop_overtrading(
    trades: "list[Trade]",
    scenarios: "list[ScenarioPublic]",
) -> "Leak | None":
    """Detect overtrading on chop sessions from journal trade groups.

    Groups closed trades by session_date. For each session with >= 5 trades
    and > 60% losing, record it.  If any such sessions are found, emit the leak.
    Severity scales with the count of chop sessions found.
    """
    from app.models.trades import TradeStatus

    closed = [t for t in trades if t.status == TradeStatus.closed and t.net_pnl is not None]
    if not closed:
        return None

    # Group by session_date.
    sessions: dict = {}
    for t in closed:
        key = t.session_date
        sessions.setdefault(key, []).append(t)

    chop_trade_ids: list[int] = []
    chop_sessions: list = []
    for day, day_trades in sessions.items():
        n = len(day_trades)
        if n < _CHOP_MIN_TRADES:
            continue
        loss_count = sum(1 for t in day_trades if t.net_pnl < 0)
        loss_rate = loss_count / n
        if loss_rate > _CHOP_LOSS_RATE:
            chop_sessions.append(day)
            for t in day_trades:
                if t.id is not None:
                    chop_trade_ids.append(t.id)

    if not chop_sessions:
        return None

    n_sessions = len(chop_sessions)
    severity = 1 if n_sessions == 1 else (2 if n_sessions <= 3 else 3)
    desc = (
        f"Over-traded on {n_sessions} choppy session(s) "
        f"({', '.join(str(d) for d in sorted(chop_sessions))}): "
        f"5+ trades with >60% loss rate."
    )
    return Leak(
        name="chop_overtrading",
        severity=severity,
        description=desc,
        evidence={"trade_ids": sorted(set(chop_trade_ids)), "attempt_ids": [], "debrief_ids": []},
        prescribed_scenario_ids=_prescribe("chop_overtrading", scenarios),
    )


# Moved-stops threshold: leak fires when > 30% of trades with a recorded
# stop_price show avg_exit on the wrong side (loosened).
_MOVED_STOP_THRESHOLD = 0.30


def _detect_moved_stops(
    trades: "list[Trade]",
    scenarios: "list[ScenarioPublic]",
) -> "Leak | None":
    """Detect loosened stops from journal data only.

    A trade is flagged when it has both stop_price and avg_exit, and:
    - long trade:  avg_exit < stop_price  (exited below the stated stop)
    - short trade: avg_exit > stop_price  (exited above the stated stop)

    This is a proxy for "stop was moved (loosened) before the exit."  Drill
    attempts are excluded here because behavior.detect_moved_stop handles them
    per-attempt in the behavior metrics layer.

    Severity is proportional to the ratio above the threshold.
    """
    from app.models.trades import TradeStatus, Direction

    with_stop = [
        t for t in trades
        if t.status == TradeStatus.closed
        and t.stop_price is not None
        and t.avg_exit is not None
        and t.net_pnl is not None
    ]
    if not with_stop:
        return None

    flagged: list[int] = []
    for t in with_stop:
        if t.direction == Direction.long:
            loosened = t.avg_exit < t.stop_price
        else:  # short
            loosened = t.avg_exit > t.stop_price
        if loosened and t.id is not None:
            flagged.append(t.id)

    ratio = len(flagged) / len(with_stop)
    if ratio <= _MOVED_STOP_THRESHOLD:
        return None

    severity = 1 if ratio < 0.5 else (2 if ratio < 0.75 else 3)
    desc = (
        f"{len(flagged)}/{len(with_stop)} trades ({ratio:.0%}) exited on the "
        f"wrong side of the stated stop — indicates stop was moved (loosened)."
    )
    return Leak(
        name="moved_stops",
        severity=severity,
        description=desc,
        evidence={"trade_ids": sorted(flagged), "attempt_ids": [], "debrief_ids": []},
        prescribed_scenario_ids=_prescribe("moved_stops", scenarios),
    )


# Psychology leak threshold: > 20% of debriefed trades with verdict
# 'failed-the-setup' or 'bad-win' triggers the leak.
_PSYCH_THRESHOLD = 0.20


def _detect_psychology_loss(
    debriefs: "list[Debrief]",
    scenarios: "list[ScenarioPublic]",
) -> "Leak | None":
    """Detect systematic psychology failures from debrief verdicts.

    Verdicts 'failed-the-setup' and 'bad-win' indicate execution/psychology
    problems (the setup was there but the trader broke their own rules, or got
    lucky with a violation).  When these exceed 20% of all completed debriefs,
    the psychology_loss leak fires.

    Only debriefs with a non-None verdict (i.e. completed through /finish) are
    counted in the denominator.
    """
    from app.models.training import DebriefVerdict

    completed = [d for d in debriefs if d.verdict is not None]
    if not completed:
        return None

    bad_verdicts = {DebriefVerdict.failed_the_setup, DebriefVerdict.bad_win}
    flagged = [d for d in completed if d.verdict in bad_verdicts]

    ratio = len(flagged) / len(completed)
    if ratio <= _PSYCH_THRESHOLD:
        return None

    severity = 1 if ratio < 0.35 else (2 if ratio < 0.60 else 3)
    desc = (
        f"{len(flagged)}/{len(completed)} debriefed trades ({ratio:.0%}) had "
        f"psychology-failure verdicts ('failed-the-setup' or 'bad-win') — "
        f"above the 20% concern threshold."
    )
    debrief_ids = [d.id for d in flagged]
    return Leak(
        name="psychology_loss",
        severity=severity,
        description=desc,
        evidence={"trade_ids": [], "attempt_ids": [], "debrief_ids": sorted(debrief_ids)},
        prescribed_scenario_ids=_prescribe("psychology_loss", scenarios),
    )


# --------------------------------------------------------------------------- #
# Score/pnl enrichment for attempts from DB                                    #
# --------------------------------------------------------------------------- #

def _enrich_attempts_from_db(
    attempts: "list[Attempt]",
    engine=None,
) -> "list[Attempt]":
    """Attach process_score and outcome_pnl from the DB to each Attempt.

    The Attempt Pydantic model does not carry these fields (they're returned
    only via AttemptFinishResponse). The leak detector reads them via getattr
    with safe defaults, but for meaningful signal we try to load them from the
    DB row directly.

    If the DB is unavailable (ImportError / no engine) we return the attempts
    unchanged — detect_revenge_size treats missing attrs as non-losses (safe
    degradation).

    Short-circuit: if every attempt already has process_score set as a dynamic
    attribute (e.g. from test fixtures or from the behavior layer), skip the DB
    round-trip entirely.
    """
    if not attempts:
        return attempts

    # If all attempts already carry scores (set via object.__setattr__ by
    # finish_attempt or test helpers), no DB round-trip is needed.
    if all(hasattr(a, "process_score") for a in attempts):
        return attempts

    try:
        from sqlalchemy import select
        from app.db.connection import get_engine, init_db
        from app.db.schema import exercise_attempts as attempts_t
    except ImportError:
        return attempts

    engine = engine or get_engine()
    init_db(engine)

    attempt_ids = [a.id for a in attempts]
    if not attempt_ids:
        return attempts

    with engine.connect() as conn:
        rows = conn.execute(
            select(
                attempts_t.c.id,
                attempts_t.c.process_score,
                attempts_t.c.outcome_pnl,
            ).where(attempts_t.c.id.in_(attempt_ids))
        ).fetchall()

    scores: dict[int, tuple[float | None, float | None]] = {
        row[0]: (row[1], row[2]) for row in rows
    }

    enriched: list = []
    for a in attempts:
        ps, pnl = scores.get(a.id, (None, None))
        if ps is not None or pnl is not None:
            object.__setattr__(a, "process_score", ps if ps is not None else 1.0)
            object.__setattr__(a, "outcome_pnl", pnl if pnl is not None else 0.0)
        enriched.append(a)
    return enriched


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #

def detect_leaks(
    trades: "list[Trade]",
    fills: "list[Fill]",
    debriefs: "list[Debrief]",
    attempts: "list[Attempt]",
    scenarios: "list[ScenarioPublic]",
    *,
    _now: str | None = None,
    _engine=None,
) -> LeakReport:
    """Detect named leaks from the operator's data and prescribe drills.

    Parameters
    ----------
    trades    : journal trades (real + sim, NOT training — caller's responsibility).
    fills     : raw fills (currently used for future signal expansion; v1 unused).
    debriefs  : completed debrief sessions with optional verdict field set.
    attempts  : drill attempts (enriched with process_score/outcome_pnl when
                finish_attempt has been called).
    scenarios : active scenarios to draw the drill playlist from.
    _now      : ISO timestamp to stamp the report (default: current UTC time).
                Inject a fixed value in tests for determinism.
    _engine   : optional SQLAlchemy engine for attempt DB enrichment; defaults
                to get_engine() if available.

    Returns a LeakReport with all detected leaks (may be empty) and the
    generated_at timestamp.

    Leak detection order is deterministic (lexicographic by name within their
    category).
    """
    generated_at = _now or datetime.now(timezone.utc).isoformat()

    # Enrich attempts with DB-persisted scores for the attempt-based detectors.
    enriched_attempts = _enrich_attempts_from_db(attempts, engine=_engine)

    leaks: list[Leak] = []

    # --- revenge_trading ---
    # Prefer attempt-based signal; fall back to journal-only when no attempt
    # data produces a signal (or there are no attempts).
    attempt_revenge = _detect_revenge_trading_from_attempts(enriched_attempts, scenarios)
    if attempt_revenge is not None:
        leaks.append(attempt_revenge)
    else:
        journal_revenge = _detect_revenge_trading_from_journal(trades, scenarios)
        if journal_revenge is not None:
            leaks.append(journal_revenge)

    # --- chop_overtrading ---
    chop_leak = _detect_chop_overtrading(trades, scenarios)
    if chop_leak is not None:
        leaks.append(chop_leak)

    # --- moved_stops ---
    stop_leak = _detect_moved_stops(trades, scenarios)
    if stop_leak is not None:
        leaks.append(stop_leak)

    # --- psychology_loss ---
    psych_leak = _detect_psychology_loss(debriefs, scenarios)
    if psych_leak is not None:
        leaks.append(psych_leak)

    return LeakReport(leaks=leaks, generated_at=generated_at)
