"""LLM seam for the M6 debrief interview (docs/PLAN-M6-TRAINING.md §2).

`base.py` is the PM-frozen contract. The backends are data-eng-owned:
`form.py` (task 6.0.3, default), `claude_cli.py` (6.2.1),
`anthropic_api.py` (6.2.2), plus `context.py` (build_debrief_context, 6.0.3).
"""
from app.llm.base import DebriefContext, LLMProvider, get_llm_provider

__all__ = ["DebriefContext", "LLMProvider", "get_llm_provider"]
