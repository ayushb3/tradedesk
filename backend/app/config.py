"""Application settings, loaded from environment / .env.

Secrets (Webull keys) live only here, sourced from env. Never logged.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env — resolved to an absolute path so uvicorn (cwd=backend/) and
# standalone scripts (cwd=repo root) both find the same file.
# config.py lives at backend/app/config.py → three .parent hops reach the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRADEDESK_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Literal["mock", "webull"] = "mock"
    # Market-data source for candles/option bars (TRADEDESK_MARKET_DATA).
    # "mock" (default) needs no keys. "alpaca" uses the read-only Alpaca
    # market-data REST API (keys: APCA_API_KEY_ID / APCA_API_SECRET_KEY).
    market_data: Literal["mock", "alpaca"] = "mock"
    # Alpaca stock feed (TRADEDESK_ALPACA_FEED): sip (default, full volume) | iex.
    # Falls back to iex on 403 and for windows ending within 15 min of now.
    alpaca_feed: str = "sip"
    # The one desk DB (TRADEDESK_DB_PATH). Relative values resolve against the
    # repo root, never the cwd, so `make dev`, scripts and the MCP server all
    # open the same file with no flags.
    db_path: str = "data/desk.db"
    tz: str = "America/New_York"
    # Wall-clock zone for times shown to the user (TRADEDESK_DISPLAY_TZ). The
    # trading session/day logic stays on `tz` (exchange time); this only moves
    # displayed clock times, e.g. the time-of-day buckets. Matches the frontend's
    # DISPLAY_TZ (frontend/src/lib/format.ts).
    display_tz: str = "America/Los_Angeles"
    watch_tickers: str = "RIVN,HOOD"
    or_minutes: int = 5

    # Browser origins allowed by CORS (TRADEDESK_CORS_ORIGINS, comma-separated).
    # Defaults to the local Vite dev server. When exposing the backend directly
    # (e.g. a Cloudflare tunnel that hits :9000 rather than proxying via Vite),
    # add the public origin here. The Vite-proxied tunnel path stays same-origin
    # and does not need this.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Optional "open original lesson" link (TRADEDESK_LESSON_URL_TEMPLATE).
    # Placeholders: {lesson_id}, {module}. Empty (default) = no link is shown.
    lesson_url_template: str = ""

    # LLM backend for the M6 debrief interview (TRADEDESK_LLM). Default "none"
    # keeps the zero-credential guarantee: the form backend runs the same
    # 4-step interview with no LLM. "claude-cli" / "api" are explicit opt-ins
    # that send trade + candle data (never credentials) to Anthropic.
    # See docs/PLAN-M6-TRAINING.md §2 and app/llm/base.py.
    llm: Literal["none", "claude-cli", "api"] = "none"

    # Webull (only used when mode == "webull"); read from WEBULL_* env vars.
    webull_app_key: str = ""
    webull_app_secret: str = ""
    webull_region: str = "us"
    # Directory for the persisted Webull access token (token.txt).
    # Set via WEBULL_OPENAPI_TOKEN_DIR env var; defaults to data/webull-token/
    # under the repo root (resolved to an absolute path at runtime by the script
    # and the provider).  Relative values are interpreted relative to the
    # process cwd — use an absolute path in production.
    webull_token_dir: str = ""

    # Anthropic API key (only used when TRADEDESK_LLM=api). Read from
    # ANTHROPIC_API_KEY env var (no TRADEDESK_ prefix, matching WEBULL_* pattern).
    # Never raise at import time — only AnthropicAPIProvider.__init__ raises
    # when the key is missing and the backend is actually selected.
    # Privacy: trade + candle data leaves the machine when TRADEDESK_LLM=api.
    # Credentials never leave. See app/llm/anthropic_api.py and
    # docs/PLAN-M6-TRAINING.md §2.
    anthropic_api_key: str = ""

    # Optional folder of private course notes (TRADEDESK_COURSE_NOTES_DIR). Read at
    # runtime by the MCP course tools; never copied into the repo. Empty = tools say
    # "course notes not configured".
    course_notes_dir: str = ""

    # Optional private folder for the Learn drills + results (TRADEDESK_LEARN_DIR). Holds
    # drills/<date>/*.json, results.jsonl and builder-state.json; never in the repo.
    # Empty = every /api/learn endpoint answers "learn folder not configured".
    learn_dir: str = ""

    @field_validator("db_path")
    @classmethod
    def _resolve_db_path(cls, v: str) -> str:
        p = Path(v).expanduser()
        return str(p if p.is_absolute() else _REPO_ROOT / p)

    @property
    def watch_list(self) -> list[str]:
        return [t.strip().upper() for t in self.watch_tickers.split(",") if t.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    # Webull secrets come from bare WEBULL_* env vars (no TRADEDESK_ prefix),
    # so pydantic-settings' prefix-scoped .env loader doesn't pick them up.
    # Load .env explicitly and read the un-prefixed vars ourselves.
    import os

    from dotenv import load_dotenv

    # override=False so real shell env wins over the file — matches how
    # `TRADEDESK_MODE=webull make dev` is expected to work.
    load_dotenv(_ENV_FILE, override=False)

    s = Settings()
    s.webull_app_key = os.getenv("WEBULL_APP_KEY", s.webull_app_key)
    s.webull_app_secret = os.getenv("WEBULL_APP_SECRET", s.webull_app_secret)
    s.webull_region = os.getenv("WEBULL_REGION", s.webull_region)
    s.webull_token_dir = os.getenv("WEBULL_OPENAPI_TOKEN_DIR", s.webull_token_dir)
    # Privacy note: trade data leaves the machine when TRADEDESK_LLM=api.
    # Credentials never leave. See PLAN-M6-TRAINING.md §2.
    s.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", s.anthropic_api_key)
    return s
