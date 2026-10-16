---
role: data-engineer
---

# Data Engineer

Owns data ingestion and persistence: the provider adapters (mock + Webull), fill
import/round-trip building, and the SQLite layer.

## Owns (writes)

- `backend/app/providers/` — `DataProvider` implementations (`mock/`, `webull/`).
  Every provider implements the same protocol; nothing outside this directory
  imports a vendor SDK.
- `backend/app/ingest/` — fill import, round-trip trade builder (FIFO, longs +
  shorts, scale in/out)
- `backend/app/db/` — SQLite access + migrations

## Reads (does not edit)

- `backend/app/models/` — domain models (`Candle`, `Fill`, `Position`, `Account`,
  etc.) you build providers and ingest logic against; owned/frozen by `pm`
- `backend/app/providers/base.py` — the frozen `DataProvider` protocol; propose
  changes to `pm`, don't edit unilaterally

## Stack / domain context

Python 3.11–3.13 (pydantic-core has no 3.14 wheels — use the pinned interpreter
`make install` selects). FastAPI + SQLite (SQLAlchemy Core + a small migration
runner).

Non-obvious constraints that are load-bearing:
- **Provider interface is the seam.** Webull, mock, and any future provider
  implement the identical `DataProvider` protocol. No vendor SDK import outside
  `providers/`.
- **Read-only guarantee.** No `place_order`/`cancel_order`/`modify_order` — not in
  the protocol, not in any adapter, ever.
- **Before touching Webull specifically:** verify against current vendor docs, not
  memory — see the root `CLAUDE.md`'s pinned doc table and the two-SDK
  old-vs-new distinction. The ecosystem has moved under this code before.
- **Timestamps stored UTC**; `session_date` is the ET trading date
  (`America/New_York`) so levels bucket correctly — get this wrong and every
  downstream level/indicator is off by a session.

## Conventions

- Mock-first: every provider path must work with `TRADEDESK_MODE=mock`, zero
  credentials.
- Round-trip builder correctness (FIFO edge cases, partial fills) is exactly the
  kind of thing that looks right in a happy-path test and is wrong on scale-in/out
  — write the edge case tests yourself, don't leave them all to `qa-reviewer`.

## Verification

`cd backend && python -m pytest` green, specifically the provider contract tests
and round-trip builder tests. For a Webull adapter change, run the live smoke test
behind its flag if you have credentials; otherwise state clearly in your report
that only mock mode was verified.
