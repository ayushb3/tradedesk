"""Prompt builders for the Trade Critic (M9a, issue #58).

Pure functions — no I/O, no LLM calls. `build_critic_prompt` packs the trade
context (summary + bar-timestamped fills + candles + enrichment + optional
debrief) and the assembled course curriculum context into a system+user pair, and
instructs the model to return a single JSON object matching the R8 shape.

The critic scores PROCESS ADHERENCE to course rules ONLY — never P&L (R11). The
model is asked to reference lessons by `source_citation_lesson_id` (an int from
the provided KNOWLEDGE CONTEXT); `critic.py` resolves that id back to the full
`KnowledgeSearchHit` and demotes to null when unverified.
"""
from __future__ import annotations

from datetime import timezone

# Cap raw transcript-ish fields so a single critique stays within token limits
# (mirrors gen_prompts._MAX_RAW_CHARS).
_MAX_RAW_CHARS = 6000


def _iso_utc(dt) -> str:
    """Render a datetime as an ISO-8601 UTC string. Empty when None."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _fmt_trade_summary(trade) -> str:
    label = getattr(trade, "contract_label", None) or trade.symbol
    direction = getattr(getattr(trade, "direction", None), "value", None) or getattr(
        trade, "direction", ""
    )
    return (
        f"Trade #{getattr(trade, 'id', '?')}: {label} ({trade.symbol}), "
        f"direction={direction}, qty={getattr(trade, 'qty', '?')}, "
        f"opened={_iso_utc(getattr(trade, 'open_ts', None))}, "
        f"closed={_iso_utc(getattr(trade, 'close_ts', None))}, "
        f"stated_stop={getattr(trade, 'stop_price', None)}, "
        f"status={getattr(getattr(trade, 'status', None), 'value', getattr(trade, 'status', ''))}"
    )


def _fmt_fills(fills) -> str:
    lines = []
    for f in fills or []:
        side = getattr(getattr(f, "side", None), "value", None) or getattr(f, "side", "")
        lines.append(
            f"- {_iso_utc(getattr(f, 'ts', None))} | {side} "
            f"{getattr(f, 'qty', '?')} @ {getattr(f, 'price', '?')} "
            f"({getattr(f, 'symbol', '')})"
        )
    return "\n".join(lines) if lines else "(no fills recorded)"


def _fmt_candles(candles, *, max_bars: int = 120) -> str:
    """Bar-timestamped OHLC so the model can pin evidence_bar_ref to a real bar."""
    rows = list(candles or [])[:max_bars]
    lines = []
    for c in rows:
        lines.append(
            f"- {_iso_utc(getattr(c, 'ts', None))} | "
            f"O={getattr(c, 'open', '?')} H={getattr(c, 'high', '?')} "
            f"L={getattr(c, 'low', '?')} C={getattr(c, 'close', '?')} "
            f"V={getattr(c, 'volume', '?')}"
        )
    return "\n".join(lines) if lines else "(no candles available)"


def _fmt_enrichment(enrichment) -> str:
    if enrichment is None:
        return "(none)"
    try:
        data = enrichment.model_dump()
    except AttributeError:
        data = dict(enrichment) if enrichment else {}
    parts = []
    for key in ("notes", "grade", "checklist", "screenshots"):
        val = data.get(key)
        if val:
            parts.append(f"{key}: {val}")
    return "; ".join(parts)[:_MAX_RAW_CHARS] if parts else "(none)"


def _fmt_knowledge_context(knowledge) -> str:
    """Numbered 'lesson_id | citation | passage-excerpt' list from knowledge.hits."""
    if knowledge is None or not getattr(knowledge, "hits", None):
        return "(no lessons in context — you MUST use \"unclear\" and cite no lessons)"
    lines = []
    for i, hit in enumerate(knowledge.hits, start=1):
        excerpt = (hit.text or "").strip().replace("\n", " ")[:600]
        lines.append(f"{i}. lesson_id={hit.lesson_id} | {hit.citation} | {excerpt}")
    concepts = getattr(knowledge, "concepts", None) or []
    concept_line = (
        f"\n\nCONCEPTS TAUGHT (context): {', '.join(concepts)}" if concepts else ""
    )
    return "\n".join(lines) + concept_line


def _fmt_debrief(debrief) -> str:
    if not debrief:
        return ""
    parts = []
    for key in ("plan", "feelings", "verdict"):
        val = debrief.get(key) if isinstance(debrief, dict) else getattr(debrief, key, None)
        if val:
            parts.append(f"{key}: {val}")
    if not parts:
        return ""
    return "DEBRIEF (the trader's own account):\n" + "\n".join(parts)[:_MAX_RAW_CHARS]


def build_critic_prompt(
    trade_ctx,
    knowledge,
    debrief=None,
    *,
    max_chars: int = _MAX_RAW_CHARS,
) -> tuple[str, str]:
    """Return (system_prompt, user_message) for the Trade Critic.

    ``trade_ctx`` is a DebriefContext (trade + fills + candles_around +
    enrichment + tags). ``knowledge`` is a KnowledgeContext (available/status/query/hits/
    concepts). ``debrief`` is an optional dict/obj with plan/feelings/verdict.
    """
    system = (
        "You are a disciplined trading COACH critiquing ONE trade against a "
        "curriculum of trading rules (the knowledge base). You return a "
        "structured critique that a student reviews alongside their chart.\n\n"
        "ANTI-GAMBLING CLAUSE (non-negotiable): Evaluate PROCESS ADHERENCE to "
        "the user's course material ONLY. Never praise or blame based on P&L or whether the trade "
        "made or lost money. A rule-following loser is a GOOD trade; a "
        "rule-breaking winner is a BAD trade. Do not mention dollars, P&L, "
        "profit, or loss in any verdict or rule_check. Phrase every verdict as a "
        "falsifiable rule-check.\n\n"
        "ABSTENTION IS REWARDED: when the chart/data do not CLEARLY show whether "
        "a rule was followed, use adherence \"unclear\". A wrong confident call "
        "(\"pass\"/\"fail\") is WORSE than \"unclear\". Only claim \"pass\" or "
        "\"fail\" when the provided bars/fills make it falsifiable.\n\n"
        "CITATION RULE: You may only reference lessons whose lesson_id appears "
        "in the provided KNOWLEDGE CONTEXT list. NEVER invent lesson ids. If the KNOWLEDGE "
        "CONTEXT is empty, cite nothing (source_citation_lesson_id = null) and "
        "lean on \"unclear\".\n\n"
        "Return a SINGLE JSON object and nothing else — no prose, no markdown, "
        "no code fences. Shape:\n"
        "{\n"
        '  "verdict_by_phase": {\n'
        '    "setup": "<short markdown rule-check verdict>",\n'
        '    "entry": "<short markdown rule-check verdict>",\n'
        '    "management": "<short markdown rule-check verdict>",\n'
        '    "exit": "<short markdown rule-check verdict>"\n'
        "  },\n"
        '  "rule_checks": [\n'
        "    {\n"
        '      "rule": "<the course rule being checked>",\n'
        '      "adherence": "pass" | "fail" | "unclear",\n'
        '      "source_citation_lesson_id": <int lesson_id from KNOWLEDGE CONTEXT, or null>,\n'
        '      "evidence_bar_ref": "<ISO-8601 UTC timestamp of the pinned bar, or null>",\n'
        '      "note": "<short falsifiable rationale — no P&L>"\n'
        "    }\n"
        "  ],\n"
        '  "prescription": {\n'
        '    "drill_id": <int scenario id, or null>,\n'
        '    "drill_spec": <a new-drill object (see below), or null>,\n'
        '    "readings": [<int lesson_id from KNOWLEDGE CONTEXT>, ...]\n'
        "  }\n"
        "}\n\n"
        "PRESCRIPTION — provide EITHER drill_id (reference an existing scenario) "
        "OR drill_spec (a NEW drill to author) — NEVER both; omit both (null) if "
        "no drill is warranted. When no existing drill_id fits the mistake you "
        "found, emit a drill_spec so the trader can practice it. drill_spec shape "
        "(minimal ScenarioCreate for a real trade):\n"
        "{\n"
        '  "title": "<short drill title naming the skill to practice>",\n'
        '  "source_type": "debrief",\n'
        '  "drill_type": "decision-point" | "management-only" | "do-nothing" | '
        '"spot-setup" | "drawdown-script",\n'
        '  "symbol": "<the trade symbol>",\n'
        '  "session_date": "<YYYY-MM-DD of the trade session>",\n'
        '  "source_trade_id": <this trade id>,\n'
        '  "answer_key": {\n'
        '    "correct_actions": ["skip" | "wait" | "enter_long" | "enter_short" | '
        '"exit" | "set_stop" | "move_stop", ...],\n'
        '    "explanation": "<why that is the correct process — rule-based, no P&L>"\n'
        "  }\n"
        "}\n\n"
        "Rules:\n"
        "- source_citation_lesson_id must be an int lesson_id present in the KNOWLEDGE "
        "CONTEXT list, or null. Do not emit a citation object.\n"
        "- evidence_bar_ref must be one of the candle timestamps provided (ISO "
        "UTC), or null if the check doesn't pin to a bar. Do not fabricate a "
        "timestamp.\n"
        "- readings are course lesson ids drawn ONLY from the KNOWLEDGE CONTEXT.\n"
        "- A drill_spec must use source_type \"debrief\" and reference THIS "
        "trade's symbol, session_date, and id — never invent another tape.\n"
        "- Keep every verdict/note terse and falsifiable. No P&L language."
    )

    trade = trade_ctx.trade
    fills = getattr(trade_ctx, "fills", [])
    candles = getattr(trade_ctx, "candles_around", [])
    enrichment = getattr(trade_ctx, "enrichment", None)
    tags = getattr(trade_ctx, "tags", [])
    tag_names = ", ".join(t.name for t in (tags or [])) or "(none)"

    debrief_block = _fmt_debrief(debrief)

    user_sections = [
        "TRADE SUMMARY:\n" + _fmt_trade_summary(trade),
        f"SETUP TAGS: {tag_names}",
        "FILLS (bar-timestamped, ISO UTC):\n" + _fmt_fills(fills),
        "CANDLES AROUND THE TRADE (ISO UTC per bar):\n" + _fmt_candles(candles),
        "ENRICHMENT:\n" + _fmt_enrichment(enrichment),
    ]
    if debrief_block:
        user_sections.append(debrief_block)
    user_sections.append(
        "KNOWLEDGE CONTEXT (numbered; cite lessons ONLY by these lesson_ids):\n"
        f"query: {getattr(knowledge, 'query', '')}\n"
        + _fmt_knowledge_context(knowledge)
    )

    user = "\n\n".join(user_sections)
    return system, user
