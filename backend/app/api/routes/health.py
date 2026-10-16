from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import settings_dep
from app.config import Settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Settings = Depends(settings_dep)) -> dict:
    return {"status": "ok", "mode": settings.mode, "tz": settings.tz}
