#!/usr/bin/env python
"""One-time Webull access-token bootstrap script.

Run this ONCE before switching TRADEDESK_MODE=webull.  It drives the
create-token → SMS-verify → NORMAL flow documented in the Webull OpenAPI
authentication guide, then writes the verified token to disk so the
WebullProvider can pick it up at runtime without any interactive step.

Usage
-----
    python backend/scripts/webull_auth.py [--token-dir /abs/path]

The script reads credentials from the same environment variables / .env file
that the main app uses (WEBULL_APP_KEY, WEBULL_APP_SECRET, WEBULL_REGION,
WEBULL_OPENAPI_TOKEN_DIR).  Pass --token-dir to override the directory.

Polling defaults: check every 5 s, give up after 5 min (300 s).  Both are
configurable via --interval and --timeout.

Exit codes
----------
  0  Token status is NORMAL; token written to <token_dir>/token.txt.
  1  Timeout waiting for verification (token stays PENDING; run again).
  2  Unexpected error (bad credentials, network, etc.).
"""
from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve repo root so we can import the app package and set a sensible
# default token_dir even when invoked from any cwd.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_TOKEN_DIR = _REPO_ROOT / "data" / "webull-token"

# Add repo's backend directory to sys.path so `from app.config import ...` works
# whether the script is run from the repo root or elsewhere.
sys.path.insert(0, str(_REPO_ROOT / "backend"))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Bootstrap a Webull OpenAPI access token (one-time setup).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--token-dir",
        default=None,
        help=(
            "Directory where token.txt is stored. "
            "Overrides WEBULL_OPENAPI_TOKEN_DIR and the default "
            f"({_DEFAULT_TOKEN_DIR})."
        ),
    )
    p.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Seconds between status-check polls (default: 5).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Maximum seconds to wait for SMS verification (default: 300).",
    )
    return p.parse_args()


def _resolve_token_dir(args_token_dir: str | None, settings_token_dir: str) -> Path:
    """Return an absolute Path for the token directory, in priority order:
    CLI flag > WEBULL_OPENAPI_TOKEN_DIR env var (via settings) > default.
    """
    if args_token_dir:
        return Path(args_token_dir).expanduser().resolve()
    if settings_token_dir.strip():
        return Path(settings_token_dir.strip()).expanduser().resolve()
    return _DEFAULT_TOKEN_DIR.resolve()


def run(
    app_key: str,
    app_secret: str,
    region: str,
    token_dir: Path,
    interval_seconds: int,
    timeout_seconds: int,
    *,
    _token_operation_class=None,  # injectable for tests
) -> int:
    """Core logic — separated from CLI glue so tests can call it directly.

    Returns an exit code: 0 = NORMAL, 1 = timeout, 2 = error.
    """
    try:
        from webull.core.client import ApiClient  # type: ignore
        from webull.core.http.initializer.token.token_manager import TokenManager  # type: ignore
        from webull.core.http.initializer.token.token_operation import TokenOperation  # type: ignore
    except ImportError:
        print(
            "ERROR: webull-openapi-python-sdk is not installed.\n"
            "       Run: pip install webull-openapi-python-sdk",
            file=sys.stderr,
        )
        return 2

    # Ensure token directory exists (mirrors TokenStorage._ensure_dir_exists).
    token_dir.mkdir(parents=True, exist_ok=True)
    token_file = token_dir / "token.txt"

    print(f"Token directory : {token_dir}")
    print(f"Token file      : {token_file}")
    print()

    # Build ApiClient — no endpoint override needed; the SDK resolves from region.
    api_client = ApiClient(
        app_key,
        app_secret,
        region,
        token_check_duration_seconds=timeout_seconds,
        token_check_interval_seconds=interval_seconds,
    )
    api_client.set_token_dir(str(token_dir))

    # Allow test injection of a fake TokenOperation.
    TokenOpClass = _token_operation_class or TokenOperation
    token_op = TokenOpClass(api_client)

    # Step 1: Read any existing token from disk to pass as the "local token"
    # hint.  The server may return NORMAL immediately if it's still valid.
    local_token: str | None = None
    if token_file.exists():
        try:
            local_token = token_file.read_text(encoding="utf-8").splitlines()[0].strip() or None
            if local_token:
                print(f"Found existing token on disk — sending to server for validation.")
        except OSError:
            local_token = None

    # Step 2: Create (or re-create) the token.
    print("Calling token/create …")
    try:
        create_resp = token_op.create_token(local_token)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: token/create failed — {exc}", file=sys.stderr)
        return 2

    if create_resp.status_code != 200:
        print(
            f"ERROR: token/create returned HTTP {create_resp.status_code}.\n"
            "       Check WEBULL_APP_KEY / WEBULL_APP_SECRET and try again.",
            file=sys.stderr,
        )
        return 2

    token_data = create_resp.json()
    if not token_data or not token_data.get("token"):
        print("ERROR: token/create returned an empty response body.", file=sys.stderr)
        return 2

    pending_token: str = token_data["token"]
    status: str = token_data.get("status", "")
    expires: int = token_data.get("expires", 0)

    print(f"Token received  : …{pending_token[-6:]}")
    print(f"Status          : {status}")

    if status == "NORMAL":
        # Token is still valid — nothing more to do.
        print("\nToken is already NORMAL. Writing to disk.")
        _write_token(token_file, pending_token, expires, status)
        _print_success(token_file, expires)
        return 0

    if status in ("INVALID", "EXPIRED"):
        print(
            f"\nERROR: Token status is {status}. This can happen if the previous\n"
            "       token was revoked. Delete the token file and run this script\n"
            f"       again: rm {token_file}",
            file=sys.stderr,
        )
        return 2

    # Step 3: PENDING — prompt the user and poll.
    print(
        "\nAction required:\n"
        "  1. Open the Webull mobile app.\n"
        "  2. Tap Messages → OpenAPI Notifications → Check Now.\n"
        "  3. Enter the SMS verification code shown there.\n"
        "\n"
        f"Polling for NORMAL every {interval_seconds}s "
        f"(timeout in {timeout_seconds}s) …"
    )

    deadline = time.monotonic() + timeout_seconds
    poll_count = 0

    while time.monotonic() < deadline:
        time.sleep(interval_seconds)
        poll_count += 1
        elapsed = int(poll_count * interval_seconds)

        try:
            check_resp = token_op.check_token(pending_token)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{elapsed:>4}s] check_token error — {exc}", file=sys.stderr)
            continue

        if check_resp.status_code != 200:
            print(
                f"  [{elapsed:>4}s] check_token HTTP {check_resp.status_code} — retrying …"
            )
            continue

        check_data = check_resp.json()
        if not check_data:
            print(f"  [{elapsed:>4}s] empty response — retrying …")
            continue

        status = check_data.get("status", "")
        expires = check_data.get("expires", 0)

        print(f"  [{elapsed:>4}s] status = {status}")

        if status == "NORMAL":
            # Verified — write to disk and exit.
            verified_token: str = check_data.get("token", pending_token)
            _write_token(token_file, verified_token, expires, status)
            print()
            _print_success(token_file, expires)
            return 0

        if status in ("INVALID", "EXPIRED"):
            print(
                f"\nERROR: Token status is {status}. Verification failed or was\n"
                "       rejected.  Run this script again to generate a new token.",
                file=sys.stderr,
            )
            return 2

        # Still PENDING — keep polling.

    print(
        f"\nTimeout: token was not verified within {timeout_seconds}s.\n"
        "Run this script again and complete the SMS verification in the app.",
        file=sys.stderr,
    )
    return 1


def _write_token(token_file: Path, token: str, expires: int, status: str) -> None:
    """Write token, expires, and status to disk (same format as TokenManager)."""
    token_file.parent.mkdir(parents=True, exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(token + "\n")
        f.write(str(expires) + "\n")
        f.write(status + "\n")


def _print_success(token_file: Path, expires: int) -> None:
    expiry_str = ""
    if expires:
        try:
            expiry_dt = datetime.datetime.fromtimestamp(
                expires / 1000, tz=datetime.timezone.utc
            )
            expiry_str = f" (expires {expiry_dt.strftime('%Y-%m-%d %H:%M UTC')})"
        except (OSError, OverflowError, ValueError):
            pass
    print(f"Token status    : NORMAL{expiry_str}")
    print(f"Token file      : {token_file}")
    print()
    print("You can now run: TRADEDESK_MODE=webull make dev")


def main() -> None:
    args = _parse_args()

    # Import settings after path is set up.
    from app.config import Settings  # type: ignore

    settings = Settings()

    # Resolve creds (pydantic-settings reads WEBULL_* env vars automatically
    # only if the field prefix matches; these don't, so we pull them manually
    # like get_settings() does in config.py).
    import os

    app_key = os.getenv("WEBULL_APP_KEY", settings.webull_app_key).strip()
    app_secret = os.getenv("WEBULL_APP_SECRET", settings.webull_app_secret).strip()
    region = os.getenv("WEBULL_REGION", settings.webull_region).strip() or "us"
    settings_token_dir = os.getenv(
        "WEBULL_OPENAPI_TOKEN_DIR", settings.webull_token_dir
    )

    if not app_key or not app_secret:
        print(
            "ERROR: WEBULL_APP_KEY and WEBULL_APP_SECRET must be set.\n"
            "       Add them to your .env file or export them in your shell.",
            file=sys.stderr,
        )
        sys.exit(2)

    token_dir = _resolve_token_dir(args.token_dir, settings_token_dir)

    exit_code = run(
        app_key=app_key,
        app_secret=app_secret,
        region=region,
        token_dir=token_dir,
        interval_seconds=args.interval,
        timeout_seconds=args.timeout,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
