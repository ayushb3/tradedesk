"""Minimal read-only Alpaca market-data REST client (httpx).

Only ``https://data.alpaca.markets`` GET endpoints for bars. There is no
trading client here and no order/position/account endpoint, by design.
Keys travel in headers only and never appear in logs or exception messages.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Iterator

import httpx

from .errors import AlpacaConfigError, AlpacaHTTPError

DATA_BASE_URL = "https://data.alpaca.markets"
_MAX_RETRIES = 2
_TIMEOUT = 20.0


def load_keys() -> tuple[str, str]:
    key = os.environ.get("APCA_API_KEY_ID", "").strip()
    secret = os.environ.get("APCA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise AlpacaConfigError(
            "TRADEDESK_MARKET_DATA=alpaca needs APCA_API_KEY_ID and APCA_API_SECRET_KEY "
            "in the environment or the repo .env (market-data keys only)."
        )
    return key, secret


class AlpacaDataClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._sleep = sleep
        self._client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        if self._client is None:
            key, secret = load_keys()
            self._client = httpx.Client(
                base_url=DATA_BASE_URL,
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                timeout=_TIMEOUT,
                transport=self._transport,
            )
        return self._client

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET with 429 backoff (<=2 retries). Raises AlpacaHTTPError otherwise."""
        http = self._http()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = http.get(path, params=params)
            except httpx.HTTPError as exc:
                raise AlpacaHTTPError(0, f"transport error: {type(exc).__name__}") from None
            if resp.status_code == 429 and attempt < _MAX_RETRIES:
                self._sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise AlpacaHTTPError(resp.status_code)
            return resp.json()
        raise AlpacaHTTPError(429)  # pragma: no cover

    def pages(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        params = dict(params)
        while True:
            body = self.get(path, params)
            yield body
            token = body.get("next_page_token")
            if not token:
                return
            params["page_token"] = token
