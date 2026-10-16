"""Read-only Webull OpenAPI provider package.

Exports :class:`WebullProvider` (selected by ``settings.mode == "webull"`` in
:func:`app.providers.base.get_provider`) and the module's typed errors. The
Webull SDK is imported lazily inside the provider, so importing this package
never requires the SDK to be installed.
"""
from __future__ import annotations

from .errors import (
    WebullAuthError,
    WebullConfigError,
    WebullError,
    WebullSDKNotInstalled,
    WebullSubscriptionError,
    WebullTokenPending,
)
from .provider import WebullProvider

__all__ = [
    "WebullProvider",
    "WebullError",
    "WebullConfigError",
    "WebullSDKNotInstalled",
    "WebullTokenPending",
    "WebullAuthError",
    "WebullSubscriptionError",
]
