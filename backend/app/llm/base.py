"""LLMProvider interface (frozen v1) + factory — the M6 debrief-interview brain.

The single seam between the app and any LLM backend. Form (no LLM), claude-cli
(subscription), or anthropic API implements this Protocol; the interview
protocol, artifacts, and schema are identical across backends, so swapping
`TRADEDESK_LLM` never changes behavior contracts — exactly like DataProvider
mock ↔ webull. Nothing outside `app/llm/` may import an LLM SDK or shell out
to an LLM CLI.

INTERVIEW PROTOCOL ONLY: there is intentionally no free-form generate /
complete / chat method. Backends emit `InterviewTurn`s for the fixed
four-step debrief arc (setup → plan → feelings → verdict) and their writes
go only through validated save paths. Do not add generation methods.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.models.market import Candle
from app.models.trades import Fill, Tag, Trade, TradeEnrichment
from app.models.training import DebriefReply, InterviewTurn


class DebriefContext(BaseModel):
    """Everything the interviewer knows BEFORE asking anything.

    Questions must be specific, not generic (PLAN-M6-TRAINING.md §1): the
    agent already has the trade, its fills, the candles around it, any prior
    enrichment/tags, and the rule checklist the trade is graded against.
    Assembled by `app.llm.context.build_debrief_context` (data-eng, 6.0.3).
    """

    trade: Trade
    fills: list[Fill] = Field(default_factory=list)
    candles_around: list[Candle] = Field(default_factory=list)
    enrichment: TradeEnrichment = Field(default_factory=TradeEnrichment)
    tags: list[Tag] = Field(default_factory=list)
    # Rule checklist this trade is scored against: [{"item": str, "checked": bool}]
    checklist_template: list[dict] = Field(default_factory=list)


@runtime_checkable
class LLMProvider(Protocol):
    # Backend identifier persisted on debriefs.llm_backend: "none" | "claude-cli" | "api".
    name: str

    def start_interview(self, ctx: DebriefContext) -> InterviewTurn: ...

    def next_turn(
        self, ctx: DebriefContext, transcript: list[dict], reply: DebriefReply
    ) -> InterviewTurn: ...


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Return the active LLM backend based on settings.llm. Imports are lazy
    so a missing backend module never breaks the others; until a backend
    lands, a clear NotImplementedError names the owning task (routes map
    NotImplementedError to HTTP 501). Backends take (settings) to construct,
    same convention as DataProvider implementations.
    """
    settings = settings or get_settings()
    if settings.llm == "claude-cli":
        try:
            from app.llm.claude_cli import ClaudeCLIProvider
        except ImportError as exc:
            raise NotImplementedError(
                "claude-cli LLM backend not implemented (data-eng task 6.2.1)"
            ) from exc
        return ClaudeCLIProvider(settings)
    if settings.llm == "api":
        try:
            from app.llm.anthropic_api import AnthropicAPIProvider
        except ImportError as exc:
            raise NotImplementedError(
                "anthropic API LLM backend not implemented (data-eng task 6.2.2)"
            ) from exc
        try:
            return AnthropicAPIProvider(settings)
        except RuntimeError as exc:
            # Missing ANTHROPIC_API_KEY etc. — treat as "backend not configured"
            # (501) rather than an unhandled server error (500). The debrief
            # route surfaces NotImplementedError as 501 with the message.
            raise NotImplementedError(str(exc)) from exc
    # Default: "none" — the form backend. Same 4-step interview as an
    # adaptive structured form. No LLM, no credentials, zero privacy cost.
    try:
        from app.llm.form import FormProvider
    except ImportError as exc:
        raise NotImplementedError(
            "form (TRADEDESK_LLM=none) backend not implemented (data-eng task 6.0.3)"
        ) from exc
    return FormProvider(settings)
