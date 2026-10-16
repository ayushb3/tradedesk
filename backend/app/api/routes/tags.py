"""Setup-tag CRUD (M3).

Contract seam (data-engineer owns app/ingest/tags.py):
    list_tags() -> list[Tag]
    create_tag(payload: TagCreate) -> Tag
    update_tag(tag_id, payload: TagUpdate) -> Tag | None
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.trades import Tag, TagCreate, TagUpdate

router = APIRouter(tags=["tags"])


@router.get("/tags", response_model=list[Tag])
def list_tags() -> list[Tag]:
    try:
        from app.ingest.tags import list_tags as _list
    except ImportError:
        raise HTTPException(status_code=501, detail="tags not implemented")
    return _list()


@router.post("/tags", response_model=Tag)
def create_tag(payload: TagCreate) -> Tag:
    try:
        from app.ingest.tags import create_tag as _create
    except ImportError:
        raise HTTPException(status_code=501, detail="tags not implemented")
    return _create(payload)


@router.patch("/tags/{tag_id}", response_model=Tag)
def update_tag(tag_id: int, payload: TagUpdate) -> Tag:
    try:
        from app.ingest.tags import update_tag as _update
    except ImportError:
        raise HTTPException(status_code=501, detail="tags not implemented")
    t = _update(tag_id, payload)
    if t is None:
        raise HTTPException(status_code=404, detail="tag not found")
    return t
