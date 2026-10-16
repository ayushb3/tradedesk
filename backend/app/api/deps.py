"""FastAPI dependencies."""
from __future__ import annotations

from app.config import Settings, get_settings
from app.providers.base import DataProvider, get_provider


def settings_dep() -> Settings:
    return get_settings()


def provider_dep() -> DataProvider:
    return get_provider(get_settings())
