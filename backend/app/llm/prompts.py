"""Shared prompt-construction helpers for the M6 debrief interview.

Factored out of claude_cli.py (task 6.2.1) so both ClaudeCLIProvider and
AnthropicAPIProvider use the identical prompt without duplication. Any future
prompt changes land here once and both backends pick them up.

All functions are pure: no I/O, no LLM calls, fully testable.

Privacy note: the assembled prompts embed trade facts (symbol, price, P&L, tags)
and candle context. When TRADEDESK_LLM=api this content leaves the machine and
is billed at developer pay-per-token rates — see docs/PLAN-M6-TRAINING.md §2.
Credentials (API keys, Webull tokens) NEVER appear in prompts; that is enforced
by test_llm_anthropic_api.py::test_secrets_not_in_prompt_or_logs.

data-eng tasks 6.2.1 / 6.2.2.
"""
from __future__ import annotations

from app.llm.base import DebriefContext
from app.models.training import DebriefReply

# --------------------------------------------------------------------------- #
# Trade-fact serialisation helpers
# --------------------------------------------------------------------------- #

def _candle_summary(ctx: DebriefContext) -> str:
    """Summarise candles_around as a single line (not raw bars).

    Format: "N bars around entry, hi=<max_high>, lo=<min_low>"
    Falls back gracefully when candles_around is empty.
    """
    candles = ctx.candles_around
    if not candles:
        return "no candle data available"
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    return (
        f"{len(candles)} bars around entry, "
        f"hi={max(highs):.2f}, lo={min(lows):.2f}"
    )


def _trade_summary(ctx: DebriefContext) -> str:
    """Serialise the key trade facts the agent must reference in its questions."""
    t = ctx.trade
    direction = t.direction.value if hasattr(t.direction, "value") else str(t.direction)
    open_ts_str = t.open_ts.strftime("%Y-%m-%d %H:%M UTC") if t.open_ts else "unknown"

    parts = [
        f"Symbol: {t.symbol}",
        f"Direction: {direction}",
        f"Avg entry: {t.avg_entry:.4f}",
        f"Open: {open_ts_str}",
    ]
    if t.avg_exit is not None:
        parts.append(f"Avg exit: {t.avg_exit:.4f}")
    if t.net_pnl is not None:
        sign = "+" if t.net_pnl >= 0 else ""
        parts.append(f"Net P&L: {sign}{t.net_pnl:.2f}")
    if t.r_multiple is not None:
        parts.append(f"R multiple: {t.r_multiple:.2f}")
    if t.stop_price is not None:
        parts.append(f"Recorded stop: {t.stop_price:.4f}")
    if t.hold_time_s is not None:
        parts.append(f"Hold time: {t.hold_time_s}s")
    if ctx.tags:
        tag_names = ", ".join(tag.name for tag in ctx.tags)
        parts.append(f"Tags: {tag_names}")
    parts.append(f"Candles: {_candle_summary(ctx)}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Prompt templates
# --------------------------------------------------------------------------- #

_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert trading coach conducting a structured post-trade debrief interview.

## Interview arc (four fixed steps)
1. setup   — Ask what confluence / setup the trader saw that justified the entry.
2. plan    — Ask about the stated stop, target, and R at entry; cross-check drift vs actual fills.
3. feelings — Ask about the trader's emotional state at entry and mid-trade.
4. verdict  — Ask for the trader's forced verdict: did the setup fail, or did they fail the setup?

## Rules
- Every question MUST reference concrete facts from the trade data below (symbol, entry price,
  R-multiple, tags, or candle context). Never ask generic questions.
- Respond ONLY with a single JSON object matching this exact shape:
  {{
    "step": "<setup|plan|feelings|verdict|done>",
    "question": "<the question text, or null when step==done>",
    "options": ["<option1>", ...] or null,
    "context_note": "<optional drift or factual note>",
    "done": false
  }}
- No markdown. No explanation. No code fences. Only the raw JSON object.

## Trade data
{trade_summary}

## Available verdict options (for the verdict step)
setup-failed | failed-the-setup | good-loss | bad-win | clean

## Tool-use note (v1)
In this version all context is pre-loaded above. Full tool wiring for
save_enrichment / create_scenario is deferred — v1 stays interview-only.
Artifacts are persisted by finish_debrief after the interview completes.
"""

_NEXT_TURN_USER_TEMPLATE = """\
## Transcript so far
{transcript_text}

## Latest user reply (step: {reply_step})
{reply_text}

Based on the transcript and the trade data in your system prompt, produce the NEXT interview turn.
Respond ONLY with a single JSON object matching the InterviewTurn shape described in your system
prompt. If the interview arc is complete (all four steps answered), emit:
{{"step":"done","question":null,"options":null,"context_note":null,"done":true}}
"""

_START_USER_TEMPLATE = """\
The interview is starting. The trade data is in your system prompt.
Produce the FIRST interview turn: the setup question.
Respond ONLY with a single JSON object (step="setup") referencing concrete facts from the trade.
"""


# --------------------------------------------------------------------------- #
# Transcript formatting helpers
# --------------------------------------------------------------------------- #

def _format_transcript(transcript: list[dict]) -> str:
    """Render the transcript list as readable text for the next-turn prompt."""
    lines: list[str] = []
    for entry in transcript:
        role = entry.get("role", "?")
        step = entry.get("step", "")
        text = entry.get("text", "")
        lines.append(f"[{role}/{step}]: {text}")
    return "\n".join(lines) if lines else "(empty)"


def _infer_reply_step(transcript: list[dict]) -> str:
    """Infer the step of the last agent turn (the one the user is replying to)."""
    for entry in reversed(transcript):
        if entry.get("role") == "agent":
            return entry.get("step", "setup")
    return "setup"


# --------------------------------------------------------------------------- #
# Public API — used by ClaudeCLIProvider and AnthropicAPIProvider
# --------------------------------------------------------------------------- #

def build_prompt_start(ctx: DebriefContext) -> tuple[str, str]:
    """Return (system_prompt, user_message) for start_interview.

    Pure function — testable without any LLM invocation.
    """
    system = _SYSTEM_PROMPT_TEMPLATE.format(trade_summary=_trade_summary(ctx))
    user = _START_USER_TEMPLATE
    return system, user


def build_prompt_next(
    ctx: DebriefContext,
    transcript: list[dict],
    reply: DebriefReply,
) -> tuple[str, str]:
    """Return (system_prompt, user_message) for next_turn.

    Pure function — testable without any LLM invocation.
    """
    system = _SYSTEM_PROMPT_TEMPLATE.format(trade_summary=_trade_summary(ctx))
    reply_step = _infer_reply_step(transcript)
    user = _NEXT_TURN_USER_TEMPLATE.format(
        transcript_text=_format_transcript(transcript),
        reply_step=reply_step,
        reply_text=reply.text or "(no text)",
    )
    return system, user
