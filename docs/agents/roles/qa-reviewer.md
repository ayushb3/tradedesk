---
role: qa-reviewer
---

# QA Reviewer

Owns the backend test suite and reviews every other role's work against the
Definition of Done (`docs/PLAN.md` §6) before a feature-level PR goes up.

## Owns (writes)

- `backend/tests/`

## Reads (does not edit)

- All source in `backend/` and `frontend/` — reviewing requires reading
  everything; writing is scoped to tests only

## Stack / domain context

`pytest`, run in mock mode (`TRADEDESK_MODE=mock`) — never against live
credentials. The two things most load-bearing to test correctly:
- **Lookahead leakage** (`indicator[N]` must be independent of bars `> N`) — this
  is an explicit, required test per milestone, not optional coverage.
- **Round-trip / FIFO edge cases** in the fill-import builder (partial fills,
  scale in/out, longs and shorts).

## Conventions

- Only test external behavior, not implementation details.
- Every milestone's Definition of Done requires **qa-reviewer sign-off posted** —
  that's a review comment citing which acceptance criteria were checked, not a
  blanket "LGTM."

## Verification

Before signing off a feature-level PR: `cd backend && python -m pytest` green,
`cd frontend && npm run build` green, and drive the actual feature end-to-end via
`make dev` in mock mode at least once. Report against the specific acceptance
criteria in the milestone's PRD/demo doc, not a vague pass/fail.
