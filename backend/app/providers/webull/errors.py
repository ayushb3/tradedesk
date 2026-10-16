"""Typed errors for the Webull provider.

The API layer turns any :class:`WebullError` into a user-facing HTTP error, so
every message here is written to be *actionable* by an operator (what to fix,
where). Secret values are never included in a message.
"""
from __future__ import annotations


class WebullError(Exception):
    """Base class for every Webull-provider failure."""


class WebullConfigError(WebullError):
    """Credentials / configuration are missing or invalid."""


class WebullSDKNotInstalled(WebullError):
    """The official Webull OpenAPI SDK (webull-openapi-python-sdk) is not importable.

    Raised lazily, only when a live call is attempted — never at import time —
    so ``mode=mock`` works without the SDK present.

    Install with: ``pip install webull-openapi-python-sdk``
    """


class WebullTokenPending(WebullError):
    """The Webull access token is missing, PENDING, or EXPIRED.

    The token is obtained by running the one-time auth script and completing
    SMS verification in the Webull mobile app. Until the token reaches NORMAL
    status, no authenticated API call can succeed.

    Actionable message format::

        Run `python backend/scripts/webull_auth.py` and verify via the Webull
        mobile app (Messages → OpenAPI Notifications → Check Now → enter SMS
        code). Token dir: {token_dir}.
    """


class WebullAuthError(WebullError):
    """Authentication with the Webull OpenAPI failed (bad/expired keys)."""


class WebullSubscriptionError(WebullError):
    """The account lacks the data entitlement required for the request.

    US-stock market data over the OpenAPI requires the **OpenAPI Quotes
    Subscription** (purchased at https://www.webullapp.com/quote — this is
    separate from any in-app Webull quote subscription).

    Note: the live US API returns HTTP 401 (not 403 as docs imply) for this
    error, with body "Insufficient permission, please subscribe to stock
    quotes."  The adapter classifies by response body, not HTTP status, so
    both 401 and 403 with a subscription-signal message map here.
    """


__all__ = [
    "WebullError",
    "WebullConfigError",
    "WebullSDKNotInstalled",
    "WebullTokenPending",
    "WebullAuthError",
    "WebullSubscriptionError",
]
