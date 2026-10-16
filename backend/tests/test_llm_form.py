"""Tests for the FormProvider state machine and summarize_transcript helper.

Verifies:
1. start_interview returns step=setup with a question referencing trade facts.
2. Three next_turn calls advance: setup→plan, plan→feelings, feelings→verdict.
3. A fourth next_turn call returns step=done with done=True.
4. stop-drift context_note fires when avg_exit diverges from stop_price.
5. summarize_transcript extracts verdict/feelings/plan from a completed transcript.
6. build_debrief_context raises ValueError for an unknown trade_id.
7. FormProvider satisfies the LLMProvider Protocol (runtime_checkable).

data-eng task 6.0.3.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

UTC = ZoneInfo("UTC")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _make_trade(
    *,
    symbol: str = "RIVN",
    direction: str = "long",
    avg_entry: float = 12.00,
    avg_exit: float | None = 12.50,
    stop_price: float | None = 11.80,
    net_pnl: float | None = 50.0,
) -> "Trade":
    """Build a minimal Trade fixture."""
    from app.models.trades import Direction, Trade, TradeEnrichment, TradeStatus

    return Trade(
        id=1,
        symbol=symbol,
        direction=Direction(direction),
        open_ts=datetime(2026, 6, 10, 13, 35, 0, tzinfo=UTC),
        close_ts=datetime(2026, 6, 10, 14, 10, 0, tzinfo=UTC),
        session_date=date(2026, 6, 10),
        qty=100.0,
        avg_entry=avg_entry,
        avg_exit=avg_exit,
        gross_pnl=net_pnl,
        fees=0.35,
        net_pnl=net_pnl,
        pnl_pct=0.042 if net_pnl else None,
        stop_price=stop_price,
        r_multiple=2.5 if (avg_exit and stop_price) else None,
        hold_time_s=2100,
        status=TradeStatus.closed,
        simulated=False,
        tags=[],
        enrichment=TradeEnrichment(),
    )


def _make_ctx(trade=None) -> "DebriefContext":
    """Build a DebriefContext wrapping *trade* (or a default fixture trade)."""
    from app.llm.base import DebriefContext

    t = trade or _make_trade()
    return DebriefContext(
        trade=t,
        fills=[],
        candles_around=[],
        enrichment=t.enrichment,
        tags=t.tags,
        checklist_template=[],
    )


def _make_transcript_up_to(step: str, *, with_user: bool = True) -> list[dict]:
    """Build a synthetic transcript that contains an agent turn at *step*."""
    steps_before = {
        "setup": [],
        "plan": ["setup"],
        "feelings": ["setup", "plan"],
        "verdict": ["setup", "plan", "feelings"],
        "done": ["setup", "plan", "feelings", "verdict"],
    }
    transcript: list[dict] = []
    for s in steps_before[step]:
        transcript.append({"role": "agent", "step": s, "text": f"Agent {s} question"})
        if with_user:
            transcript.append({"role": "user", "step": s, "text": f"User {s} reply"})
    transcript.append({"role": "agent", "step": step, "text": f"Agent {step} question"})
    return transcript


# --------------------------------------------------------------------------- #
# FormProvider — state machine
# --------------------------------------------------------------------------- #

class TestFormProviderStateMachine:
    def setup_method(self):
        from app.llm.form import FormProvider
        self.provider = FormProvider()
        self.ctx = _make_ctx()

    def test_start_interview_returns_setup(self):
        turn = self.provider.start_interview(self.ctx)
        assert turn.step.value == "setup"
        assert turn.done is False
        assert turn.question is not None

    def test_start_interview_question_references_symbol(self):
        turn = self.provider.start_interview(self.ctx)
        assert "RIVN" in turn.question

    def test_start_interview_question_references_avg_entry(self):
        turn = self.provider.start_interview(self.ctx)
        assert "12.00" in turn.question

    def test_next_turn_setup_to_plan(self):
        transcript = _make_transcript_up_to("setup")
        reply = _make_reply("I saw a clean breakout above PDH.")
        turn = self.provider.next_turn(self.ctx, transcript, reply)
        assert turn.step.value == "plan"
        assert turn.done is False

    def test_next_turn_plan_to_feelings(self):
        transcript = _make_transcript_up_to("plan")
        reply = _make_reply("Stop at 11.80, target 12.60, risking 1R.")
        turn = self.provider.next_turn(self.ctx, transcript, reply)
        assert turn.step.value == "feelings"
        assert turn.options is not None
        assert "calm" in turn.options
        assert "rushed" in turn.options

    def test_next_turn_feelings_to_verdict(self):
        transcript = _make_transcript_up_to("feelings")
        reply = _make_reply("calm", fields={"selected": ["calm"]})
        turn = self.provider.next_turn(self.ctx, transcript, reply)
        assert turn.step.value == "verdict"
        assert turn.options is not None
        # Every DebriefVerdict value must appear in options
        from app.models.training import DebriefVerdict
        for v in DebriefVerdict:
            assert v.value in turn.options

    def test_next_turn_verdict_to_done(self):
        transcript = _make_transcript_up_to("verdict")
        reply = _make_reply("clean")
        turn = self.provider.next_turn(self.ctx, transcript, reply)
        assert turn.step.value == "done"
        assert turn.done is True
        assert turn.question is None

    def test_full_walk_setup_plan_feelings_verdict_done(self):
        """Walk all four steps and verify the arc end-to-end."""
        # step 1: start_interview
        turn = self.provider.start_interview(self.ctx)
        assert turn.step.value == "setup"
        transcript = [{"role": "agent", "step": "setup", "text": turn.question}]

        # step 2: reply to setup → plan
        transcript.append({"role": "user", "step": "setup", "text": "Saw PDH break"})
        turn = self.provider.next_turn(self.ctx, transcript, _make_reply("Saw PDH break"))
        assert turn.step.value == "plan"
        transcript.append({"role": "agent", "step": "plan", "text": turn.question})

        # step 3: reply to plan → feelings
        transcript.append({"role": "user", "step": "plan", "text": "Stop 11.80"})
        turn = self.provider.next_turn(self.ctx, transcript, _make_reply("Stop 11.80"))
        assert turn.step.value == "feelings"
        transcript.append({"role": "agent", "step": "feelings", "text": turn.question})

        # step 4: reply to feelings → verdict
        transcript.append({"role": "user", "step": "feelings", "text": "calm"})
        turn = self.provider.next_turn(self.ctx, transcript, _make_reply("calm"))
        assert turn.step.value == "verdict"
        transcript.append({"role": "agent", "step": "verdict", "text": turn.question})

        # step 5: reply to verdict → done
        transcript.append({"role": "user", "step": "verdict", "text": "clean"})
        turn = self.provider.next_turn(self.ctx, transcript, _make_reply("clean"))
        assert turn.step.value == "done"
        assert turn.done is True


# --------------------------------------------------------------------------- #
# Drift detection
# --------------------------------------------------------------------------- #

class TestStopDriftDetection:
    def test_no_drift_when_exit_above_stop_long(self):
        """Long trade exits above stop: no drift."""
        trade = _make_trade(avg_exit=12.50, stop_price=11.80)
        from app.llm.form import _detect_stop_drift
        assert _detect_stop_drift(trade) is None

    def test_drift_when_exit_below_stop_long(self):
        """Long trade exits below stop: flag it."""
        trade = _make_trade(avg_exit=11.50, stop_price=11.80)
        from app.llm.form import _detect_stop_drift
        note = _detect_stop_drift(trade)
        assert note is not None
        assert "11.80" in note
        assert "11.50" in note

    def test_drift_when_exit_above_stop_short(self):
        """Short trade exits above stop: flag it."""
        trade = _make_trade(
            direction="short",
            avg_entry=12.00,
            avg_exit=12.50,
            stop_price=12.20,
        )
        from app.llm.form import _detect_stop_drift
        note = _detect_stop_drift(trade)
        assert note is not None
        assert "12.20" in note

    def test_no_drift_when_stop_missing(self):
        trade = _make_trade(stop_price=None)
        from app.llm.form import _detect_stop_drift
        assert _detect_stop_drift(trade) is None

    def test_no_drift_when_exit_missing(self):
        trade = _make_trade(avg_exit=None)
        from app.llm.form import _detect_stop_drift
        assert _detect_stop_drift(trade) is None

    def test_plan_turn_has_context_note_when_drift_detected(self):
        """_plan_question includes context_note for a drifting trade."""
        trade = _make_trade(avg_exit=11.50, stop_price=11.80)
        ctx = _make_ctx(trade)
        from app.llm.form import FormProvider
        provider = FormProvider()
        transcript = _make_transcript_up_to("setup")
        turn = provider.next_turn(ctx, transcript, _make_reply("I saw a setup."))
        assert turn.context_note is not None
        assert "11.80" in turn.context_note


# --------------------------------------------------------------------------- #
# summarize_transcript
# --------------------------------------------------------------------------- #

class TestSummarizeTranscript:
    def test_empty_transcript(self):
        from app.llm.form import summarize_transcript
        enrichment, verdict, feelings, plan = summarize_transcript([])
        assert verdict is None
        assert feelings == []
        assert plan == {}
        assert enrichment.notes is None

    def test_full_transcript_extracts_verdict(self):
        from app.llm.form import summarize_transcript
        transcript = _build_full_transcript(verdict_text="clean")
        _, verdict, _, _ = summarize_transcript(transcript)
        from app.models.training import DebriefVerdict
        assert verdict == DebriefVerdict.clean

    def test_full_transcript_extracts_feelings_from_fields(self):
        from app.llm.form import summarize_transcript
        transcript = _build_full_transcript(
            feelings_fields={"selected": ["calm", "confident"]}
        )
        _, _, feelings, _ = summarize_transcript(transcript)
        feeling_values = [f["feeling"] for f in feelings]
        assert "calm" in feeling_values
        assert "confident" in feeling_values

    def test_full_transcript_extracts_plan_from_fields(self):
        from app.llm.form import summarize_transcript
        transcript = _build_full_transcript(
            plan_fields={"stop": 11.80, "target": 12.60, "r": 1.0}
        )
        _, _, _, plan = summarize_transcript(transcript)
        assert plan.get("stop") == pytest.approx(11.80)
        assert plan.get("target") == pytest.approx(12.60)

    def test_notes_assembled_from_user_turns(self):
        from app.llm.form import summarize_transcript
        transcript = _build_full_transcript(
            setup_text="saw a PDH break",
            plan_text="stop at 11.80",
        )
        enrichment, _, _, _ = summarize_transcript(transcript)
        assert enrichment.notes is not None
        assert "PDH break" in enrichment.notes
        assert "11.80" in enrichment.notes

    def test_verdict_enum_values_parseable(self):
        """All DebriefVerdict values round-trip through _parse_verdict."""
        from app.llm.form import _parse_verdict
        from app.models.training import DebriefVerdict
        for v in DebriefVerdict:
            result = _parse_verdict(v.value, {})
            assert result == v, f"Failed to parse verdict {v.value!r}"


# --------------------------------------------------------------------------- #
# build_debrief_context — error path
# --------------------------------------------------------------------------- #

def test_build_debrief_context_unknown_trade_raises(tmp_path):
    """build_debrief_context raises ValueError for an unknown trade_id."""
    from sqlalchemy import create_engine
    import app.db.connection as conn_mod
    from app.config import get_settings

    db_file = tmp_path / "ctx_test.db"

    # Point settings at the temp DB so get_trade uses it
    import os
    os.environ["TRADEDESK_DB_PATH"] = str(db_file)
    get_settings.cache_clear()
    conn_mod._engines.clear()

    try:
        from app.config import Settings
        from app.providers.mock import MockProvider
        from app.llm.context import build_debrief_context

        settings = Settings()
        provider = MockProvider(settings)

        with pytest.raises(ValueError, match="trade not found"):
            build_debrief_context(provider, trade_id=99999, settings=settings)
    finally:
        os.environ.pop("TRADEDESK_DB_PATH", None)
        get_settings.cache_clear()
        conn_mod._engines.clear()


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #

def test_form_provider_satisfies_llm_provider_protocol():
    """FormProvider must satisfy the runtime_checkable LLMProvider Protocol."""
    from app.llm.base import LLMProvider
    from app.llm.form import FormProvider

    provider = FormProvider()
    assert isinstance(provider, LLMProvider)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_reply(text: str, *, fields: dict | None = None) -> "DebriefReply":
    from app.models.training import DebriefReply
    return DebriefReply(text=text, fields=fields)


def _build_full_transcript(
    *,
    setup_text: str = "Saw a clean breakout.",
    plan_text: str = "Stop 11.80, target 12.60.",
    plan_fields: dict | None = None,
    feelings_text: str = "calm",
    feelings_fields: dict | None = None,
    verdict_text: str = "clean",
    verdict_fields: dict | None = None,
) -> list[dict]:
    """Build a complete 4-step transcript as the route would construct it."""
    transcript = [
        {"role": "agent", "step": "setup", "text": "Setup question?"},
        {"role": "user", "step": "setup", "text": setup_text,
         "fields": None},
        {"role": "agent", "step": "plan", "text": "Plan question?"},
        {"role": "user", "step": "plan", "text": plan_text,
         "fields": plan_fields},
        {"role": "agent", "step": "feelings", "text": "Feelings question?"},
        {"role": "user", "step": "feelings", "text": feelings_text,
         "fields": feelings_fields},
        {"role": "agent", "step": "verdict", "text": "Verdict question?"},
        {"role": "user", "step": "verdict", "text": verdict_text,
         "fields": verdict_fields},
    ]
    return transcript
