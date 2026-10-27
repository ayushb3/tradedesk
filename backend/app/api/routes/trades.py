"""Journal trade endpoints (M3).

Contract seam (data-engineer owns the implementation in app/ingest/):
    app.ingest.imports.import_fills(provider) -> dict          # {"fills_imported", "trades_built"}
    app.ingest.query.list_trades(**filters) -> list[Trade]
    app.ingest.query.get_trade(trade_id) -> Trade | None
    app.ingest.query.update_trade(trade_id, patch: TradeUpdate) -> Trade
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import provider_dep
from app.config import get_settings
from pydantic import BaseModel

from app.models.trades import Trade, TradeUpdate
from app.providers.base import DataProvider

router = APIRouter(tags=["trades"])


@router.post("/import/fills")
def import_fills(
    full: bool = Query(False), provider: DataProvider = Depends(provider_dep)
) -> dict:
    # NOTE: import is allowed in any mode. In mock mode it builds synthetic
    # round-trips (used by the journal test suite and for local demos); in
    # webull mode it pulls the user's real fills. The frontend only *auto*-runs
    # import in webull mode, so mock/demo data never silently masquerades as
    # the user's real trades in normal use.
    try:
        from app.ingest.imports import MixedFillSourcesError
        from app.ingest.imports import import_fills as _import
    except ImportError:
        raise HTTPException(status_code=501, detail="fill import not implemented")

    # Import typed errors lazily so mock-mode never needs the Webull package.
    from app.providers.webull.errors import (
        WebullAuthError,
        WebullSDKNotInstalled,
        WebullSubscriptionError,
        WebullTokenPending,
    )

    try:
        settings = get_settings()
        if settings.mode != "webull":
            # Mock/demo mode: a real desk.db holds Robinhood export fills, so the
            # button loads the newest local export file instead of demo data.
            from app.db.connection import get_engine, init_db
            from app.desk_mcp import writes
            from app.ingest.imports import _has_real_fills

            engine = get_engine()
            init_db(engine)
            try:
                exported = writes.import_newest_export(engine)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if exported is not None:
                return {
                    "fills_imported": exported["fills_added"],
                    "trades_built": exported["trades"],
                    "source": "robinhood_export",
                    "export_file": exported["file"],
                    "days_touched": exported["days_touched"],
                }
            with engine.connect() as conn:
                has_real = _has_real_fills(conn)
            if has_real:
                raise HTTPException(
                    status_code=404,
                    detail=(f"no Robinhood export found in {writes.export_dir().name}; "
                            "run the export first"),
                )
        return _import(provider, full=full)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="fill import not implemented")
    except MixedFillSourcesError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except WebullTokenPending as exc:
        # Token missing/PENDING/expired — user must (re)run the auth script and
        # verify via the mobile app. 503: the dependency isn't ready yet.
        raise HTTPException(status_code=503, detail=str(exc))
    except WebullSubscriptionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except WebullAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except WebullSDKNotInstalled as exc:
        raise HTTPException(status_code=501, detail=str(exc))


@router.get("/trades", response_model=list[Trade])
def list_trades(
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    symbol: str | None = None,
    tag: str | None = None,
    simulated: bool | None = None,
) -> list[Trade]:
    try:
        from app.ingest.query import list_trades as _list
    except ImportError:
        raise HTTPException(status_code=501, detail="trade query not implemented")
    return _list(from_=from_, to=to, symbol=symbol, tag=tag, simulated=simulated)


@router.get("/trades/{trade_id}", response_model=Trade)
def get_trade(trade_id: int) -> Trade:
    try:
        from app.ingest.query import get_trade as _get
    except ImportError:
        raise HTTPException(status_code=501, detail="trade query not implemented")
    t = _get(trade_id)
    if t is None:
        raise HTTPException(status_code=404, detail="trade not found")
    return t


@router.patch("/trades/{trade_id}", response_model=Trade)
def update_trade(trade_id: int, patch: TradeUpdate) -> Trade:
    try:
        from app.ingest.query import update_trade as _update
    except ImportError:
        raise HTTPException(status_code=501, detail="trade update not implemented")
    t = _update(trade_id, patch)
    if t is None:
        raise HTTPException(status_code=404, detail="trade not found")
    return t


class CallSourceBody(BaseModel):
    call_source: Literal["mentor", "own"] | None = None  # null clears


@router.put("/trades/{trade_id}/call-source", response_model=Trade)
def set_call_source(trade_id: int, body: CallSourceBody) -> Trade:
    """One-tap mentor | own on a trade (null clears). Same store as annotate_trade."""
    from app.ingest.query import update_trade as _update

    patch = TradeUpdate(call_source=body.call_source)
    patch.model_fields_set.add("call_source")
    t = _update(trade_id, patch)
    if t is None:
        raise HTTPException(status_code=404, detail="trade not found")
    return t
