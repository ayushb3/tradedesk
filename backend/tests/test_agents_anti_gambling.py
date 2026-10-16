"""Anti-gambling regression for the Trade Critic (M9a, issue #58, R11).

The M9a mirror of `test_anti_gambling_audit.py`: the critic scores PROCESS
ADHERENCE to course rules ONLY — never P&L. A rule-following LOSER must critique NO
WORSE than a rule-breaking WINNER.

Four guarantees:

1. Behavioral: run a DISCIPLINED LOSER and a LUCKY WINNER through
   `critique_trade` with a well-behaved canned critic (loser gets more `pass`
   adherences, winner more `fail`). Assert the loser ends up with strictly MORE
   `pass` rule_checks than the winner — outcome never inverts the process verdict.
2. No dollar/P&L token leaks into any verdict string or rule_check text.
3. The system prompt carries the anti-gambling clause (process-not-outcome).
4. `CriticResult` / `CriticRuleCheck` have NO P&L field (contract-level).
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone

from app.agents.critic import CriticInputs, critique_trade
from app.agents.critic_prompts import build_critic_prompt
from app.config import Settings
from app.llm.base import DebriefContext
from app.models.agents import CriticResult, CriticRuleCheck, KnowledgeContext
from app.models.knowledge import KnowledgeSearchHit
from app.models.trades import Direction, Trade, TradeStatus

from .conftest import TEST_DAY


# --------------------------------------------------------------------------- #
# Canned claude-cli runner
# --------------------------------------------------------------------------- #

def _wrap_in_cli_envelope(payload_json: str) -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "api_error_status": None,
        "result": payload_json,
    })


def _reset_binary_cache():
    import app.llm.claude_cli as cli_mod
    cli_mod._BINARY_AVAILABLE = None


def _canned_run_fn(payload: dict):
    def _run(args, *, input="", capture_output=True, text=True, timeout=60):
        if "--version" in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="claude 1.0.0-test", stderr=""
            )
        return subprocess.CompletedProcess(
            args=args, returncode=0,
            stdout=_wrap_in_cli_envelope(json.dumps(payload)), stderr="",
        )
    return _run


def _hit(lesson_id: int, text: str) -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        text=text, score=0.9, lesson_id=lesson_id, lesson_title="Lesson",
        module="7", seq=lesson_id, char_start=0, char_end=len(text),
        citation=f"Module 7 · Lesson {lesson_id:03d} — Lesson",
    )


def _trade(*, trade_id: int, net_pnl: float) -> Trade:
    return Trade(
        id=trade_id,
        symbol="AAPL",
        direction=Direction.long,
        open_ts=datetime(2026, 6, 10, 13, 30, tzinfo=timezone.utc),
        close_ts=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
        session_date=TEST_DAY,
        qty=100.0,
        avg_entry=10.0,
        avg_exit=10.0 + net_pnl / 100.0,
        net_pnl=net_pnl,
        status=TradeStatus.closed,
        simulated=True,
    )


# The course context both trades are critiqued against (same rules for both, so the
# only difference is the trade's actual process — which the canned critic scores).
_KNOWLEDGE = KnowledgeContext(
    available=True,
    status="ok",
    query="support levels reclaim risk sizing",
    hits=[
        _hit(1, "Wait for the hourly level reclaim before entering the trade."),
        _hit(2, "Size down after two consecutive losing trades to protect capital."),
        _hit(3, "Set a hard stop and honor it; never widen a stop once placed."),
    ],
    concepts=[],
)


def _critic_payload(adherences: list[str]) -> dict:
    """A well-behaved critic payload: one rule_check per course rule with the given
    adherence values. No P&L language anywhere (the critic never sees dollars)."""
    rules = [
        ("Wait for the hourly level reclaim before entering the trade.", 1,
         "Entry timing relative to the hourly level reclaim."),
        ("Size down after two consecutive losing trades to protect capital.", 2,
         "Position size relative to the sizing rule."),
        ("Set a hard stop and honor it; never widen a stop once placed.", 3,
         "Stop placement and whether it was honored."),
    ]
    return {
        "verdict_by_phase": {
            "setup": "Setup checked against the hourly level rule.",
            "entry": "Entry checked against the reclaim rule.",
            "management": "Management checked against the sizing rule.",
            "exit": "Exit checked against the stop rule.",
        },
        "rule_checks": [
            {
                "rule": rule,
                "adherence": adh,
                "source_citation_lesson_id": lid,
                "evidence_bar_ref": None,
                "note": note,
            }
            for (rule, lid, note), adh in zip(rules, adherences)
        ],
        "prescription": {"drill_id": None, "readings": []},
    }


def _count_pass(result: CriticResult) -> int:
    return sum(1 for rc in result.rule_checks if rc.adherence == "pass")


# --------------------------------------------------------------------------- #
# 1 — Behavioral: disciplined loser critiques NO WORSE than a lucky winner
# --------------------------------------------------------------------------- #

def test_disciplined_loser_critiqued_no_worse_than_lucky_winner():
    _reset_binary_cache()

    # DISCIPLINED LOSER — followed the rules, lost money. Critic (which grades
    # process, not P&L) returns all three checks as pass.
    loser_trade = _trade(trade_id=1, net_pnl=-800.0)
    loser_payload = _critic_payload(["pass", "pass", "pass"])
    loser = critique_trade(
        CriticInputs(trade_ctx=DebriefContext(trade=loser_trade), knowledge=_KNOWLEDGE),
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn(loser_payload),
    )

    # LUCKY WINNER — broke the rules, made money. Critic returns mostly fail.
    winner_trade = _trade(trade_id=2, net_pnl=+2000.0)
    winner_payload = _critic_payload(["fail", "fail", "pass"])
    winner = critique_trade(
        CriticInputs(trade_ctx=DebriefContext(trade=winner_trade), knowledge=_KNOWLEDGE),
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn(winner_payload),
    )

    assert loser is not None and winner is not None

    loser_pass = _count_pass(loser)
    winner_pass = _count_pass(winner)

    assert loser_pass > winner_pass, (
        "ANTI-GAMBLING VIOLATION: the disciplined LOSER did not critique better "
        "than the rule-breaking WINNER.\n"
        f"  Loser (net_pnl<0) pass count:  {loser_pass}\n"
        f"  Winner (net_pnl>0) pass count: {winner_pass}\n"
        "Process adherence — not P&L — must drive the verdict; the rule-following "
        "loser must never score worse than the rule-breaking winner."
    )


# --------------------------------------------------------------------------- #
# 2 — No P&L / dollar token leaks into any verdict or rule_check text
# --------------------------------------------------------------------------- #

def test_no_pnl_language_in_critic_output():
    _reset_binary_cache()

    trade = _trade(trade_id=1, net_pnl=-800.0)
    result = critique_trade(
        CriticInputs(trade_ctx=DebriefContext(trade=trade), knowledge=_KNOWLEDGE),
        settings=Settings(llm="claude-cli"),
        run_fn=_canned_run_fn(_critic_payload(["pass", "pass", "unclear"])),
    )
    assert result is not None

    # Gather every model-authored string in the result.
    strings: list[str] = [
        result.verdict_by_phase.setup,
        result.verdict_by_phase.entry,
        result.verdict_by_phase.management,
        result.verdict_by_phase.exit,
    ]
    for rc in result.rule_checks:
        strings.append(rc.rule)
        if rc.note:
            strings.append(rc.note)

    # A literal '$' or a bare P&L/profit/loss/dollar word is disallowed in the
    # rendered verdict/rule text (word-boundary so 'closed'/'lost the level' etc.
    # in fixtures don't false-trip — our fixtures avoid these words entirely).
    forbidden = [
        (r"\$", "dollar sign"),
        (r"\bpnl\b", "'pnl'"),
        (r"\bprofit\b", "'profit'"),
        (r"\bloss\b", "'loss'"),
        (r"\bdollars?\b", "'dollar(s)'"),
    ]
    violations: list[str] = []
    for s in strings:
        for pattern, desc in forbidden:
            if re.search(pattern, s, flags=re.IGNORECASE):
                violations.append(f"{desc} in: {s!r}")

    assert not violations, (
        "ANTI-GAMBLING VIOLATION: P&L / dollar language leaked into the critic "
        "output:\n  " + "\n  ".join(violations)
    )


# --------------------------------------------------------------------------- #
# 3 — The system prompt carries the process-not-outcome anti-gambling clause
# --------------------------------------------------------------------------- #

def test_prompt_contains_anti_gambling_clause():
    trade_ctx = DebriefContext(trade=_trade(trade_id=1, net_pnl=-800.0))
    system, user = build_critic_prompt(trade_ctx, _KNOWLEDGE)

    lowered = system.lower()
    # Process-not-outcome framing.
    assert "process adherence" in lowered, (
        "System prompt must instruct the critic to score PROCESS ADHERENCE."
    )
    # The rule-following-loser / rule-breaking-winner inversion is spelled out.
    assert "rule-following loser" in lowered
    assert "rule-breaking winner" in lowered
    # It must explicitly forbid judging on money — P&L named alongside 'never'.
    assert "never" in lowered
    assert re.search(r"p&l|profit|loss", lowered), (
        "System prompt must reference P&L/profit/loss when forbidding "
        "outcome-based judgment."
    )


# --------------------------------------------------------------------------- #
# 4 — Contract: CriticResult / CriticRuleCheck have NO P&L field
# --------------------------------------------------------------------------- #

def test_critic_models_have_no_pnl_field():
    banned = ("pnl", "profit", "loss", "dollar", "money", "gain")

    for name, model in (("CriticResult", CriticResult), ("CriticRuleCheck", CriticRuleCheck)):
        fields = list(model.model_fields)
        assert "pnl" not in fields, f"{name} must not expose a 'pnl' field: {fields}"
        offenders = [f for f in fields if any(b in f.lower() for b in banned)]
        assert not offenders, (
            f"ANTI-GAMBLING VIOLATION: {name} exposes outcome-keyed field(s): "
            f"{offenders}. The critic scores process, never P&L."
        )
