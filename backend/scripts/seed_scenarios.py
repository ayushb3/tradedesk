"""Seed the training-module scenario library with ~10 curated drills.

M6 task 6.1.4 — the drill lobby ships with content instead of empty.

Coverage of the seeded M3 default tags (see docs/PLAN.md §3.7):
    PDH-break, PMH-break (variant), OR-breakout, VWAP-reclaim, EMA-pullback
plus useful M6 archetypes: failed-breakout, gap-and-go, midday chop
(do-nothing), trend day (management-only), VWAP rejection (short),
PDH-break blind mode, and a gap-fill fade short.

All scenarios reference the MockProvider's tickers (RIVN, HOOD — see
``backend/app/config.py`` ``watch_tickers``) on past weekday session dates so
``get_candles(symbol, day)`` deterministically returns candles for them
(``mock/synthetic.py::generate_candles`` — the mock generates a full
04:00-20:00 ET session for any date).

Bar indices in ``ScenarioCreate`` are **global** — indices into the full
underlying 1-minute session (bar 0 == 04:00 ET, bar 330 == 09:30 ET). The
``/session`` route slices ``[start_bar:end_bar]``, and the indicators service
validates that ``decision_bar`` sits within that slice. Actions posted by the
drill player use the same global frame (see the scorer e2e test), as do
``ScenarioAnnotation.bar`` and ``AnswerKey.management_notes[*]['bar']``.

Most decision drills key on the first 5-15 minutes after regular open
(bars 330-345), matching how a 5-min OR / early-move setup resolves on
1-min bars.

Usage
-----
    python -m scripts.seed_scenarios                # idempotent: skip existing titles
    python -m scripts.seed_scenarios --dry-run      # print plan, touch nothing
    python -m scripts.seed_scenarios --force        # insert even on title collision

Idempotency: a scenario whose ``title`` already appears in the DB is skipped
by default. Use ``--force`` to re-seed after schema tweaks.

Bit-rot: tests/test_seed_scenarios.py imports ``SEED_SCENARIOS`` and checks
that every entry has a valid archetype/drill_type, at least one annotation,
a non-empty answer_key.explanation, and (for real-source scenarios) a symbol
in the MockProvider's known-ticker set.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from app.config import Settings
from app.db.training_store import create_scenario, list_scenarios
from app.models.training import (
    AnswerKey,
    Archetype,
    AttemptActionType,
    DrillType,
    ScenarioAnnotation,
    ScenarioCreate,
    ScenarioSource,
)


# --------------------------------------------------------------------------- #
# Ticker/date pool                                                             #
# --------------------------------------------------------------------------- #
# Mock provider (backend/app/providers/mock/provider.py) serves whatever the
# ``watch_tickers`` config lists — default is RIVN,HOOD. Any weekday date works
# because generate_candles derives everything deterministically from
# hash(symbol, day). Pick a stable Mon-Fri stretch in June 2026 so
# ``session_date`` is unambiguous and reproducible.
_MOCK_TICKERS = ("RIVN", "HOOD")

# All ET weekdays used across the seeds. Kept as constants so the same date
# reads clearly in every scenario definition below.
D_MON_08 = date(2026, 6, 8)   # Monday
D_TUE_09 = date(2026, 6, 9)   # Tuesday
D_WED_10 = date(2026, 6, 10)  # Wednesday
D_THU_11 = date(2026, 6, 11)  # Thursday
D_FRI_12 = date(2026, 6, 12)  # Friday
D_MON_15 = date(2026, 6, 15)  # Monday
D_TUE_16 = date(2026, 6, 16)  # Tuesday
D_WED_17 = date(2026, 6, 17)  # Wednesday
D_THU_18 = date(2026, 6, 18)  # Thursday
D_FRI_19 = date(2026, 6, 19)  # Friday


# --------------------------------------------------------------------------- #
# Seed data                                                                    #
# --------------------------------------------------------------------------- #
# Each ScenarioCreate references a real ticker+date the mock provider serves.
# start_bar=330 aligns bar 0 of the drill with 09:30 ET (regular open) unless
# a scenario specifically wants premarket/midday context.

SEED_SCENARIOS: list[ScenarioCreate] = [
    # -- 1. PDH-break decision-point --------------------------------------- #
    ScenarioCreate(
        title="PDH break: RIVN opening drive",
        archetype=Archetype.custom,
        source_type=ScenarioSource.real,
        drill_type=DrillType.decision_point,
        symbol="RIVN",
        session_date=D_MON_08,
        start_bar=330,   # 09:30 ET open
        decision_bar=335,  # bar 335 = 09:35 ET — first clean push through PDH
        end_bar=390,     # ~one hour of tape after the open
        annotations=[
            ScenarioAnnotation(
                bar=330,
                text="Regular open (09:30 ET). Prior-day high sits just overhead.",
                kind="note",
            ),
            ScenarioAnnotation(
                bar=335,
                text="First clean touch of PDH with volume — this is the trigger.",
                kind="confluence",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.enter_long],
            explanation=(
                "PDH is the cleanest bull trigger of the morning: buyers who were "
                "trapped short at yesterday's high cover, while momentum longs "
                "chase the level. Correct play at the decision bar is enter_long "
                "with a stop just below PDH."
            ),
        ),
        blind_default=False,
    ),

    # -- 2. VWAP-reclaim decision-point ------------------------------------ #
    ScenarioCreate(
        title="VWAP reclaim: RIVN morning long",
        archetype=Archetype.trend_day,
        source_type=ScenarioSource.real,
        drill_type=DrillType.decision_point,
        symbol="RIVN",
        session_date=D_TUE_09,
        start_bar=330,
        decision_bar=342,  # bar 342 = 09:42 ET — post opening-drive VWAP reclaim
        end_bar=450,
        annotations=[
            ScenarioAnnotation(
                bar=332,
                text="Early flush under VWAP — panic sellers hit the bid.",
                kind="note",
            ),
            ScenarioAnnotation(
                bar=342,
                text="Price reclaims VWAP with a stronger candle — trigger bar.",
                kind="confluence",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.enter_long],
            explanation=(
                "A VWAP reclaim from below is a high-quality mean-reversion trigger: "
                "the session's volume-weighted average is now support, and shorts "
                "from the flush are underwater. Enter_long on the reclaim candle "
                "close with a stop back under VWAP."
            ),
        ),
        blind_default=False,
    ),

    # -- 3. OR-breakout decision-point ------------------------------------- #
    ScenarioCreate(
        title="Opening-range breakout: RIVN long",
        archetype=Archetype.gap_and_go,
        source_type=ScenarioSource.real,
        drill_type=DrillType.decision_point,
        symbol="RIVN",
        session_date=D_WED_10,
        start_bar=330,
        # 5-min OR resolves at bar 335 on 1-min data; wait one bar for
        # confirmation → decision at bar 336 (09:36 ET).
        decision_bar=336,
        end_bar=420,
        annotations=[
            ScenarioAnnotation(
                bar=330,
                text="First bar of the 5-min opening range sets the initial high/low.",
                kind="note",
            ),
            ScenarioAnnotation(
                bar=335,
                text="OR high tags. Wait one bar for confirmation before firing.",
                kind="warning",
            ),
            ScenarioAnnotation(
                bar=336,
                text="Clean break-and-hold above OR high with volume — trigger.",
                kind="confluence",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.enter_long],
            explanation=(
                "The 5-minute opening range brackets the earliest institutional "
                "positioning. A confirmed close above the OR high on volume is "
                "the textbook breakout trigger. Enter_long at bar 6 with a stop "
                "back inside the OR."
            ),
        ),
        blind_default=False,
    ),

    # -- 4. Failed-breakout decision-point (skip is correct) --------------- #
    ScenarioCreate(
        title="Failed breakout: HOOD anti-FOMO drill",
        archetype=Archetype.failed_breakout,
        source_type=ScenarioSource.real,
        drill_type=DrillType.decision_point,
        symbol="HOOD",
        session_date=D_THU_11,
        start_bar=330,
        decision_bar=338,   # 09:38 ET — after second failed poke
        end_bar=420,
        annotations=[
            ScenarioAnnotation(
                bar=335,
                text="Price pokes over yesterday's high — looks like a breakout.",
                kind="warning",
            ),
            ScenarioAnnotation(
                bar=337,
                text="No volume expansion. Wick fades back inside the range.",
                kind="warning",
            ),
            ScenarioAnnotation(
                bar=338,
                text="Second attempt fails on lower volume — classic failed break.",
                kind="warning",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.skip],
            explanation=(
                "A breakout without volume expansion is a fade in disguise. Two "
                "attempts failing at the level with declining volume is a strong "
                "no-trade signal. Correct action is skip — passing this drill "
                "means recognising the fake and not chasing."
            ),
        ),
        blind_default=False,
    ),

    # -- 5. Gap-and-go decision-point -------------------------------------- #
    ScenarioCreate(
        title="Gap-and-go continuation: HOOD",
        archetype=Archetype.gap_and_go,
        source_type=ScenarioSource.real,
        drill_type=DrillType.decision_point,
        symbol="HOOD",
        session_date=D_FRI_12,
        start_bar=330,
        decision_bar=333,   # 09:33 ET — gap-and-gos resolve fast
        end_bar=420,
        annotations=[
            ScenarioAnnotation(
                bar=330,
                text="Strong gap up on catalyst. First bar closes near the high.",
                kind="confluence",
            ),
            ScenarioAnnotation(
                bar=333,
                text="Shallow pullback holds above the opening print — continuation trigger.",
                kind="confluence",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.enter_long],
            explanation=(
                "A gap-and-go with a shallow first pullback that holds the "
                "opening print is one of the highest-expectancy long triggers "
                "of the day. Enter_long on the continuation candle with a "
                "stop under the opening print."
            ),
        ),
        blind_default=False,
    ),

    # -- 6. Midday chop do-nothing drill ----------------------------------- #
    ScenarioCreate(
        title="Midday chop: do-nothing drill (RIVN)",
        archetype=Archetype.chop,
        source_type=ScenarioSource.real,
        drill_type=DrillType.do_nothing,
        symbol="RIVN",
        session_date=D_MON_15,
        # 12:00-14:00 ET = midday lull. Bar 480 = 12:00 ET (04:00 + 480 min).
        start_bar=480,
        decision_bar=480,   # whole window is scored — no single trigger bar
        end_bar=600,      # 14:00 ET
        annotations=[
            ScenarioAnnotation(
                bar=480,
                text="Midday session begins (12:00 ET). Volume drops off a cliff.",
                kind="warning",
            ),
            ScenarioAnnotation(
                bar=540,
                text="Range compresses. Every 'breakout' fails within 2-3 bars.",
                kind="warning",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.skip],
            explanation=(
                "Midday chop is where discretionary traders leak the morning's "
                "P&L back. Passing this drill means recognising the regime and "
                "taking zero trades across the entire window. Any enter_long "
                "or enter_short scores zero on decision correctness."
            ),
        ),
        blind_default=False,
    ),

    # -- 7. Trend-day management-only -------------------------------------- #
    ScenarioCreate(
        title="Trend day management: HOOD hold the runner",
        archetype=Archetype.trend_day,
        source_type=ScenarioSource.real,
        drill_type=DrillType.management_only,
        symbol="HOOD",
        session_date=D_TUE_16,
        start_bar=330,
        # Entry is forced at bar 335 (09:35 ET); user manages exit/stop from there.
        decision_bar=335,
        end_bar=690,   # 15:30 ET, into the close
        annotations=[
            ScenarioAnnotation(
                bar=335,
                text="Entry forced at breakout. Focus: managing the runner, not the entry.",
                kind="note",
            ),
            ScenarioAnnotation(
                bar=390,
                text="First pullback to EMA9 (10:30 ET) — trend intact. Do not exit here.",
                kind="confluence",
            ),
            ScenarioAnnotation(
                bar=630,
                text="Late afternoon (14:30 ET): trail stop under prior swing low, ride into close.",
                kind="note",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.exit],
            explanation=(
                "Trend days reward patience. The entry is given; your job is to "
                "resist mid-day fear exits and trail the stop under structure. "
                "Best-graded runs exit near the close (or via trailed stop-out), "
                "not on the first pullback."
            ),
            management_notes=[
                {"bar": 390, "action": "wait", "note": "First EMA9 pullback (10:30 ET) — hold."},
                {"bar": 630, "action": "move_stop", "note": "Trail stop under prior swing low (14:30 ET)."},
            ],
        ),
        blind_default=False,
    ),

    # -- 8. VWAP-rejection short decision-point ---------------------------- #
    ScenarioCreate(
        title="VWAP rejection short: RIVN",
        archetype=Archetype.failed_breakout,
        source_type=ScenarioSource.real,
        drill_type=DrillType.decision_point,
        symbol="RIVN",
        session_date=D_WED_17,
        start_bar=330,
        decision_bar=345,   # 09:45 ET — rejection candle
        end_bar=450,
        annotations=[
            ScenarioAnnotation(
                bar=340,
                text="Price grinds up toward VWAP from below on weakening volume.",
                kind="note",
            ),
            ScenarioAnnotation(
                bar=345,
                text="Rejection wick at VWAP with a strong red follow-through candle.",
                kind="confluence",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.enter_short],
            explanation=(
                "When price approaches VWAP from below and rejects with a clear "
                "wick + red follow-through, VWAP is acting as resistance and the "
                "session bias is bearish. Enter_short on the rejection with a "
                "stop just above VWAP."
            ),
        ),
        blind_default=False,
    ),

    # -- 9. PDH-break BLIND (tests tape-reading without ticker/date) ------- #
    ScenarioCreate(
        title="PDH break (blind): read the tape",
        archetype=Archetype.custom,
        source_type=ScenarioSource.real,
        drill_type=DrillType.decision_point,
        symbol="HOOD",
        session_date=D_THU_18,
        start_bar=330,
        decision_bar=335,   # 09:35 ET
        end_bar=390,
        annotations=[
            ScenarioAnnotation(
                bar=330,
                text="Opening bar. Horizontal level marked overhead — what is it?",
                kind="note",
            ),
            ScenarioAnnotation(
                bar=335,
                text="Clean push through the marked level on volume.",
                kind="confluence",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.enter_long],
            explanation=(
                "Same setup as the named PDH-break drill, but with no ticker or "
                "date to bias you. If you can identify the setup from tape and "
                "level alone, you own the pattern. The correct action is "
                "enter_long on the break-and-hold at the decision bar."
            ),
        ),
        blind_default=True,
    ),

    # -- 10. Gap-fill fade short ------------------------------------------- #
    ScenarioCreate(
        title="Gap-fill fade: RIVN short the open",
        archetype=Archetype.failed_breakout,
        source_type=ScenarioSource.real,
        drill_type=DrillType.decision_point,
        symbol="RIVN",
        session_date=D_FRI_19,
        start_bar=330,
        decision_bar=334,   # 09:34 ET — follow-through short candle
        end_bar=420,
        annotations=[
            ScenarioAnnotation(
                bar=330,
                text="Gap up open, but first bar prints a bearish engulfing wick.",
                kind="warning",
            ),
            ScenarioAnnotation(
                bar=333,
                text="Price breaks the opening print — gap starts filling.",
                kind="confluence",
            ),
            ScenarioAnnotation(
                bar=334,
                text="Follow-through short candle — trigger to short the fill.",
                kind="confluence",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.enter_short],
            explanation=(
                "A gap that fails to hold the opening print in the first few "
                "bars is a fade candidate: everyone who chased the gap is now "
                "trapped long. Enter_short on the break of the opening print "
                "with a stop above the pre-market/opening high; target is the "
                "prior close (full gap fill)."
            ),
        ),
        blind_default=False,
    ),

    # -- 11. Drawdown script: 3-session engineered losing streak -------------- #
    #
    # synth_params.sessions defines the sub-session composition.  The drill
    # player presents these as a linked sequence; the backend scorer splits
    # the flat actions_json by session index using the bar // 1000 convention:
    #   session 0 → bars 0..999
    #   session 1 → bars 1000..1999
    #   session 2 → bars 2000..2999
    #
    # decision_bar and end_bar are None here because the per-session decision
    # bars ride in answer_key.management_notes (one entry per session).
    # synth_seed=999 is a legacy field; the actual sub-session generation is
    # driven by the seeds inside synth_params.sessions.
    ScenarioCreate(
        title="Drawdown script: 3-session engineered losing streak",
        archetype=Archetype.custom,
        source_type=ScenarioSource.synthetic,
        drill_type=DrillType.drawdown_script,
        synth_seed=999,
        synth_params={
            "sessions": [
                {"archetype": "failed-breakout", "seed": 101, "atr_pct": 0.025},
                {"archetype": "chop", "seed": 102},
                {"archetype": "failed-breakout", "seed": 103, "atr_pct": 0.03},
            ]
        },
        start_bar=0,
        decision_bar=None,
        end_bar=None,
        annotations=[
            ScenarioAnnotation(
                bar=0,
                text="Session 1 of 3 — failed-breakout tape. Breakout attempt looks real but has no follow-through.",
                kind="note",
            ),
            ScenarioAnnotation(
                bar=1000,
                text="Session 2 of 3 — chop. Do-nothing scores best here. Range contracts; every poke reverses.",
                kind="note",
            ),
            ScenarioAnnotation(
                bar=2000,
                text="Session 3 of 3 — final test. You are now down on the script. Notice how you feel.",
                kind="warning",
            ),
        ],
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.skip, AttemptActionType.skip, AttemptActionType.skip],
            explanation=(
                "All three sessions are designed so that any entry loses. "
                "Correct discipline: skip all three. Your process score rewards "
                "rule adherence, not P&L — a user who followed every rule and was "
                "stopped out still earns a high score. "
                "Watch session 3 closely: size-jumping after two losses is the "
                "exact leak this drill is designed to surface."
            ),
            management_notes=[
                {"session": 0, "decision_bar": 12, "correct_action": "skip"},
                {"session": 1, "decision_bar": 30, "correct_action": "skip"},
                {"session": 2, "decision_bar": 12, "correct_action": "skip"},
            ],
        ),
        blind_default=False,
    ),
]


# --------------------------------------------------------------------------- #
# Seed runner                                                                  #
# --------------------------------------------------------------------------- #

def _summarise(sc: ScenarioCreate) -> str:
    """One-line summary for --dry-run / progress output."""
    sym = sc.symbol or "(synth)"
    dt = sc.session_date.isoformat() if sc.session_date else "-"
    blind = " [BLIND]" if sc.blind_default else ""
    return (
        f"  {sc.drill_type.value:<16} {sc.archetype.value:<16} "
        f"{sym:<6} {dt}  {sc.title}{blind}"
    )


def run(dry_run: bool = False, force: bool = False) -> tuple[int, int]:
    """Seed scenarios. Returns (inserted, skipped_existing).

    - dry_run: prints the plan, does not touch the DB.
    - force:   inserts even if a scenario with the same title already exists.
    """
    if dry_run:
        print(f"[dry-run] Would consider {len(SEED_SCENARIOS)} scenarios:")
        for sc in SEED_SCENARIOS:
            print(_summarise(sc))
        return (0, 0)

    existing_titles = {s.title for s in list_scenarios()}
    inserted = 0
    skipped = 0
    for sc in SEED_SCENARIOS:
        if sc.title in existing_titles and not force:
            skipped += 1
            continue
        create_scenario(sc)
        inserted += 1
        print(f"  + inserted: {sc.title}")

    total = len(SEED_SCENARIOS)
    if skipped and not force:
        print(f"{skipped} already present, {inserted} inserted (out of {total}).")
    else:
        print(f"{inserted} inserted (out of {total}).")
    return (inserted, skipped)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed the training-module scenario library.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without touching the DB.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Insert even if a scenario with the same title already exists.",
    )
    args = parser.parse_args()

    # Touch settings early so any config error surfaces before DB writes.
    _ = Settings()
    run(dry_run=args.dry_run, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
