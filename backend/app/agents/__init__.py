"""Coach-agent orchestration package (M9a, issue #58).

Houses the deterministic retrieval seam (`retrieve`) and the Trade Critic
pipeline (`critique_trade` / `CriticInputs`). The HTTP contract lives in
`app.models.agents`; this package is the orchestration internals behind it.
"""
from __future__ import annotations

from app.agents.critic import CriticInputs, critique_trade
from app.agents.retrieve import retrieve

__all__ = ["retrieve", "critique_trade", "CriticInputs"]
