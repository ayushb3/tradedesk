"""App config (M3). Read effective settings; persist UI overrides to the config table."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import settings_dep
from app.config import Settings

router = APIRouter(tags=["config"])


@router.get("/config")
def get_config(settings: Settings = Depends(settings_dep)) -> dict:
    base = {
        "mode": settings.mode,
        "tz": settings.tz,
        "watch_tickers": settings.watch_list,
        "or_minutes": settings.or_minutes,
    }
    # Merge any persisted overrides if the db layer is available.
    try:
        from sqlalchemy import select

        from app.db import get_engine
        from app.db.schema import config as config_tbl

        with get_engine().connect() as conn:
            for row in conn.execute(select(config_tbl.c.key, config_tbl.c.value_json)):
                base[row.key] = row.value_json
    except Exception:
        pass
    return base


@router.put("/config")
def put_config(patch: dict) -> dict:
    try:
        from sqlalchemy.dialects.sqlite import insert

        from app.db import get_engine, init_db
        from app.db.schema import config as config_tbl

        init_db()
        with get_engine().begin() as conn:
            for k, v in patch.items():
                stmt = insert(config_tbl).values(key=k, value_json=v)
                stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value_json": v})
                conn.execute(stmt)
    except Exception as e:  # db layer not ready — non-fatal for local config
        return {"ok": False, "error": str(e)}
    return {"ok": True, "updated": list(patch.keys())}
