"""FastAPI application factory. `make dev` runs uvicorn app.main:app."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    agents,
    analytics,
    candles,
    config,
    debriefs,
    desk,
    health,
    knowledge,
    plans,
    replay,
    review,
    sim,
    tags,
    trades,
    training,
)


def create_app() -> FastAPI:
    app = FastAPI(title="TradeDesk API", version="0.1.0")

    # CORS origins from settings (TRADEDESK_CORS_ORIGINS); defaults to the local
    # Vite dev server. The tunnel path proxies /api through Vite (same-origin),
    # so this only matters if the backend is exposed directly.
    from app.config import get_settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    # Webull provider error → HTTP mapping.
    # These errors can surface from any route that calls the provider, so
    # registering them here keeps the routes free of error-translation logic.
    # ------------------------------------------------------------------ #
    from app.providers.webull.errors import (
        WebullAuthError,
        WebullConfigError,
        WebullError,
        WebullSDKNotInstalled,
        WebullSubscriptionError,
        WebullTokenPending,
    )

    @app.exception_handler(WebullTokenPending)
    async def _webull_token_pending(request: Request, exc: WebullTokenPending) -> JSONResponse:
        return JSONResponse(status_code=428, content={"detail": str(exc)})

    @app.exception_handler(WebullSubscriptionError)
    async def _webull_subscription(request: Request, exc: WebullSubscriptionError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(WebullAuthError)
    async def _webull_auth(request: Request, exc: WebullAuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(WebullConfigError)
    async def _webull_config(request: Request, exc: WebullConfigError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(WebullSDKNotInstalled)
    async def _webull_sdk(request: Request, exc: WebullSDKNotInstalled) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(WebullError)
    async def _webull_generic(request: Request, exc: WebullError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    # ------------------------------------------------------------------ #

    api_prefix = "/api"
    app.include_router(health.router, prefix=api_prefix)
    app.include_router(candles.router, prefix=api_prefix)
    app.include_router(replay.router, prefix=api_prefix)  # M2
    app.include_router(sim.router, prefix=api_prefix)      # M2
    app.include_router(trades.router, prefix=api_prefix)     # M3
    app.include_router(tags.router, prefix=api_prefix)       # M3
    app.include_router(analytics.router, prefix=api_prefix)  # M3
    app.include_router(config.router, prefix=api_prefix)     # M3
    app.include_router(training.router, prefix=api_prefix)   # M6
    app.include_router(debriefs.router, prefix=api_prefix)   # M6
    app.include_router(review.router, prefix=api_prefix)     # M7a
    app.include_router(knowledge.router, prefix=api_prefix)        # M8a
    app.include_router(agents.router, prefix=api_prefix)     # M9a
    app.include_router(desk.router, prefix=api_prefix)       # Desk 1.1
    app.include_router(plans.router, prefix=api_prefix)      # Desk 2.1
    return app


app = create_app()
