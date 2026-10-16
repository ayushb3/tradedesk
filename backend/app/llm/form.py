"""FormProvider — the default (TRADEDESK_LLM=none) LLM backend.

A deterministic 4-step state machine implementing LLMProvider (see
app/llm/base.py for the frozen Protocol). No LLM. No credentials. Zero
privacy cost. Same four-step arc as every other backend: setup → plan →
feelings → verdict → done.

This module is the **source of truth** for how the form backend interprets
its own transcript. The LLM backends (claude_cli.py, anthropic_api.py) may
call `summarize_transcript` as a fallback when their tool calls fail.

Determinism guarantee: given the same DebriefContext and the same sequence
of user replies, FormProvider produces the same InterviewTurn sequence.

data-eng task 6.0.3.
"""
from __future__ import annotations

from app.config import Settings
from app.llm.base import DebriefContext
from app.models.trades import Direction, TradeEnrichment
from app.models.training import (
    DebriefReply,
    DebriefStep,
    DebriefVerdict,
    InterviewTurn,
)


class FormProvider:
    """Deterministic form-backend interview. No LLM, no credentials."""

    name: str = "none"

    def __init__(self, settings: Settings | None = None) -> None:
        # settings reserved for future parity with LLM backends; unused here.
        self._settings = settings

    # ---------------------------------------------------------------------- #
    # LLMProvider protocol
    # ---------------------------------------------------------------------- #

    def start_interview(self, ctx: DebriefContext) -> InterviewTurn:
        """Return the setup question, referencing concrete trade facts."""
        trade = ctx.trade
        direction = "long" if trade.direction == Direction.long else "short"
        open_ts_str = trade.open_ts.strftime("%Y-%m-%d %H:%M") if trade.open_ts else "unknown time"

        # Build tag hint if any tags are already attached
        tag_hint = ""
        if ctx.tags:
            names = ", ".join(t.name for t in ctx.tags)
            tag_hint = f" You tagged it: {names}."

        question = (
            f"You entered {trade.symbol} {direction} at "
            f"{trade.avg_entry:.2f} ({open_ts_str} UTC)."
            f"{tag_hint}"
            f" Walk me through the setup — what confluence did you see that"
            f" justified the entry?"
        )
        return InterviewTurn(step=DebriefStep.setup, question=question)

    def next_turn(
        self,
        ctx: DebriefContext,
        transcript: list[dict],
        reply: DebriefReply,
    ) -> InterviewTurn:
        """Advance the state machine based on the last agent step in transcript."""
        current_step = _last_agent_step(transcript)
        return _advance(current_step, ctx)


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #

def _last_agent_step(transcript: list[dict]) -> DebriefStep:
    """Return the step of the most recent agent turn in the transcript.

    Scans backward so the most recent agent turn wins. Falls back to `setup`
    if no agent turn is found (shouldn't happen in normal flow, but safe).
    """
    for entry in reversed(transcript):
        if entry.get("role") == "agent":
            step_str = entry.get("step", "setup")
            try:
                return DebriefStep(step_str)
            except ValueError:
                return DebriefStep.setup
    return DebriefStep.setup


def _advance(current_step: DebriefStep, ctx: DebriefContext) -> InterviewTurn:
    """Return the next InterviewTurn after *current_step*."""
    if current_step == DebriefStep.setup:
        return _plan_question(ctx)
    if current_step == DebriefStep.plan:
        return _feelings_question(ctx)
    if current_step == DebriefStep.feelings:
        return _verdict_question(ctx)
    # After verdict (or done) → done
    return InterviewTurn(step=DebriefStep.done, done=True, question=None)


def _plan_question(ctx: DebriefContext) -> InterviewTurn:
    """Plan step: ask about stop/target/R and cross-check against actual fills."""
    trade = ctx.trade
    question_parts: list[str] = []

    if trade.stop_price is not None:
        question_parts.append(
            f"What was your planned stop and target when you entered"
            f" {trade.symbol} at {trade.avg_entry:.2f}?"
            f" You have a stop recorded at {trade.stop_price:.2f} — was that"
            f" your original stop, or did you move it?"
        )
    else:
        question_parts.append(
            f"What was your planned stop and target when you entered"
            f" {trade.symbol} at {trade.avg_entry:.2f}?"
            f" What was the R you were risking at entry?"
        )

    question = " ".join(question_parts)

    # Drift detection: compare stated stop_price against actual avg_exit.
    # TODO(6.2.3): drift detection is heuristic — see plan §4 note
    context_note = _detect_stop_drift(trade)

    return InterviewTurn(
        step=DebriefStep.plan,
        question=question,
        context_note=context_note,
    )


def _detect_stop_drift(trade) -> str | None:
    """Return a drift note when the exit price diverges from the stated stop.

    For a long trade: if avg_exit < stop_price the exit was *below* the stop
    (stop may have been moved down, or execution beyond stop). If avg_exit >
    stop_price the trade was exited in profit before the stop was hit — also
    worth flagging because the plan question should probe this.

    For a short trade the logic inverts.

    Returns None when stop_price or avg_exit are missing.

    TODO(6.2.3): drift detection is heuristic — see plan §4 note
    """
    if trade.stop_price is None or trade.avg_exit is None:
        return None

    stop = trade.stop_price
    exit_ = trade.avg_exit
    entry = trade.avg_entry

    # Tolerance: 2 cents — below this we treat it as "at stop"
    _TOLERANCE = 0.02

    if trade.direction == Direction.long:
        # Risk side is below entry
        if exit_ < stop - _TOLERANCE:
            return (
                f"Stated stop {stop:.2f}, but you exited at {exit_:.2f}"
                f" — did you move the stop lower?"
            )
        if exit_ > stop + _TOLERANCE and exit_ < entry:
            # Exited between stop and entry (cut the loss before stop)
            return None  # early exit is good discipline, no flag needed
    else:
        # Short: risk side is above entry
        if exit_ > stop + _TOLERANCE:
            return (
                f"Stated stop {stop:.2f}, but you exited at {exit_:.2f}"
                f" — did you move the stop higher?"
            )

    return None


def _feelings_question(ctx: DebriefContext) -> InterviewTurn:
    """Feelings step: emotional state at entry/mid-trade."""
    trade = ctx.trade
    question = (
        f"How were you feeling when you took the {trade.symbol} trade?"
        f" Select everything that applies — and feel free to add context."
    )
    options = ["calm", "rushed", "frustrated", "revenge", "confident", "hesitant"]
    return InterviewTurn(
        step=DebriefStep.feelings,
        question=question,
        options=options,
    )


def _verdict_question(ctx: DebriefContext) -> InterviewTurn:
    """Verdict step: the forced distinction — setup failed vs failed the setup."""
    trade = ctx.trade

    pnl_str = ""
    if trade.net_pnl is not None:
        sign = "+" if trade.net_pnl >= 0 else ""
        pnl_str = f" (net P&L: {sign}{trade.net_pnl:.2f})"

    question = (
        f"Final verdict on {trade.symbol}{pnl_str}: how would you classify"
        f" this trade?"
    )
    options = [e.value for e in DebriefVerdict]
    return InterviewTurn(
        step=DebriefStep.verdict,
        question=question,
        options=options,
    )


# --------------------------------------------------------------------------- #
# summarize_transcript — pure interpreter of a form transcript
# --------------------------------------------------------------------------- #

def summarize_transcript(
    transcript: list[dict],
) -> tuple[TradeEnrichment, DebriefVerdict | None, list[dict], dict]:
    """Interpret a completed form transcript into structured artifacts.

    Returns (enrichment_delta, verdict, feelings, plan_json) so
    training_store.finish_debrief can write them out.

    Deterministic. Used by the 'none' backend and as a fallback for LLM
    backends when their tool calls fail (see PLAN-M6-TRAINING §2).

    transcript entries have shape:
        {"role": "agent"|"user", "step": str, "text": str,
         "fields": dict | None}  # fields present only on user turns via
                                  # DebriefReply.fields (structured answers)

    Returns:
    - enrichment_delta: TradeEnrichment with notes from all user turns
      concatenated and checklist/grade left as defaults (populated from
      ctx.enrichment in the route, not here)
    - verdict: the DebriefVerdict parsed from the user turn after the
      "verdict" agent step, or None if not found
    - feelings: list of {"feeling": str} dicts from the user turn after
      the "feelings" agent step, or [] if not found
    - plan_json: {"stop": ..., "target": ..., "notes": ...} from the
      user turn after the "plan" agent step, or {}
    """
    verdict: DebriefVerdict | None = None
    feelings: list[dict] = []
    plan_json: dict = {}
    notes_parts: list[str] = []

    # Walk through the transcript pairing agent questions with user answers.
    # The transcript is: [agent/setup, user/setup, agent/plan, user/plan, ...]
    last_agent_step: str | None = None

    for entry in transcript:
        role = entry.get("role")
        step = entry.get("step", "")
        text = entry.get("text", "")
        fields = entry.get("fields") or {}

        if role == "agent":
            last_agent_step = step
            continue

        # role == "user"
        if last_agent_step == DebriefStep.setup.value:
            if text:
                notes_parts.append(f"Setup: {text}")

        elif last_agent_step == DebriefStep.plan.value:
            plan_json = _extract_plan(text, fields)
            if text:
                notes_parts.append(f"Plan: {text}")

        elif last_agent_step == DebriefStep.feelings.value:
            feelings = _extract_feelings(text, fields)
            if text:
                notes_parts.append(f"Feelings: {text}")

        elif last_agent_step == DebriefStep.verdict.value:
            verdict = _parse_verdict(text, fields)
            if text:
                notes_parts.append(f"Verdict: {text}")

    notes = "\n\n".join(notes_parts) or None
    enrichment_delta = TradeEnrichment(notes=notes)
    return enrichment_delta, verdict, feelings, plan_json


def _extract_plan(text: str, fields: dict) -> dict:
    """Extract stop/target/notes from a plan-step user reply."""
    result: dict = {}
    if "stop" in fields:
        result["stop"] = fields["stop"]
    if "target" in fields:
        result["target"] = fields["target"]
    if "r" in fields:
        result["r"] = fields["r"]
    if text:
        result["notes"] = text
    return result


def _extract_feelings(text: str, fields: dict) -> list[dict]:
    """Parse a feelings reply into a list of {"feeling": str} dicts.

    The form backend sends options as a multi-select; free text is parsed
    by splitting on commas/slashes as a best-effort fallback.
    """
    raw: list[str] = []

    if "selected" in fields and isinstance(fields["selected"], list):
        raw = [str(v).strip().lower() for v in fields["selected"] if v]
    elif text:
        # Free-text fallback: split on comma, semicolon, slash
        import re
        raw = [s.strip().lower() for s in re.split(r"[,;/]", text) if s.strip()]

    return [{"feeling": f} for f in raw if f]


def _parse_verdict(text: str, fields: dict) -> DebriefVerdict | None:
    """Parse a verdict value from structured fields or free text."""
    raw: str | None = None

    if "verdict" in fields:
        raw = str(fields["verdict"]).strip()
    elif "selected" in fields and isinstance(fields["selected"], list):
        if fields["selected"]:
            raw = str(fields["selected"][0]).strip()
    elif text:
        raw = text.strip()

    if raw is None:
        return None

    # Try exact enum value first
    try:
        return DebriefVerdict(raw.lower())
    except ValueError:
        pass

    # Fuzzy map common phrasings
    raw_l = raw.lower()
    mapping = {
        "setup failed": DebriefVerdict.setup_failed,
        "setup-failed": DebriefVerdict.setup_failed,
        "failed the setup": DebriefVerdict.failed_the_setup,
        "failed-the-setup": DebriefVerdict.failed_the_setup,
        "good loss": DebriefVerdict.good_loss,
        "good-loss": DebriefVerdict.good_loss,
        "bad win": DebriefVerdict.bad_win,
        "bad-win": DebriefVerdict.bad_win,
        "clean": DebriefVerdict.clean,
    }
    return mapping.get(raw_l)
