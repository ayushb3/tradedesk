"""Simulated-trade endpoints (M2).

Contract seam (data-engineer owns the implementation):
    app.ingest.sim.save_sim_trade(payload: SimTradeCreate) -> Trade
    app.ingest.sim.list_sim_trades() -> list[Trade]

Persisted with simulated=True into the same trades table as live trades, so
replay practice flows through the identical journal analytics.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.trades import SimTradeCreate, Trade

router = APIRouter(tags=["sim"])


@router.post("/sim/trades", response_model=Trade)
def create_sim_trade(payload: SimTradeCreate) -> Trade:
    try:
        from app.ingest.sim import save_sim_trade
    except ImportError:
        raise HTTPException(status_code=501, detail="sim persistence not implemented")
    try:
        return save_sim_trade(payload)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="sim persistence not implemented")


@router.get("/sim/trades", response_model=list[Trade])
def get_sim_trades() -> list[Trade]:
    try:
        from app.ingest.sim import list_sim_trades
    except ImportError:
        raise HTTPException(status_code=501, detail="sim persistence not implemented")
    return list_sim_trades()
