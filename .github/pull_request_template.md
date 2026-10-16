<!--
  This is TradeDesk's own Gate 2 template (see docs/M7A-BRIEF.md and PLAN.md §4),
  now formalized as the repo's actual PR template. Applies to both sub-PRs and
  feature-level PRs — see WORKFLOW.md §6 for which one needs to wait for human
  review before merge.

  Gate 1 (contract freeze) happens before this PR exists, for anything crossing
  role-ownership boundaries (see docs/agents/roles/pm.md) — it's not a PR-body
  section, it's a prerequisite commit on the branch.
-->

## Summary

<!-- One or two sentences: what does this PR do and why. -->

## What lands

<!-- Bulleted list of the concrete changes. -->

## Tests

<!-- `cd backend && python -m pytest` / `cd frontend && npm run build` (or the
     relevant subset). If this is a sub-PR, CI green is enough detail here. -->

## Demo

<!-- Point at docs/DEMO-Mx.md (existing convention) and/or screenshots under
     docs/media/. For sub-PRs with no visible surface, "n/a" is fine. -->

## Linked issue

Closes #<!-- issue number -->

## Out of scope

<!-- What this PR deliberately does NOT do — TradeDesk PRDs already define scope
     fences (see docs/prd/PRD-M*.md); restate the relevant one here rather than
     re-deriving it. Delete this section if nothing's relevant. -->

---

- [ ] `/code-review` run
- [ ] `/interrogate` run (feature-level PRs only)
- [ ] Backend suite green, frontend build green
- [ ] Read-only-broker guarantee preserved (no order-entry code added)
