# Workflow — Doc of Record

This is the complete, standalone description of how this project runs agent-driven
work. It should be followable cold, without any other context. Project-specific
details (roles, stack, label renames) live in `CLAUDE.md` and `docs/agents/`; this
file describes the *process*, which is the same across every project that vendors
this template.

If this file and `CLAUDE.md` disagree, `CLAUDE.md` wins for anything project-specific
(stack, file paths); this file wins for anything about the process itself
(lifecycle, gates, review layers).

This is TradeDesk's first adoption of GitHub-native scaffolding — before this, work
was tracked manually via `docs/PLAN.md`'s task lists and briefs like
`docs/M7A-BRIEF.md`. Those documents stay as-is (see §4 and §9 of `docs/PLAN.md`,
and `docs/DECISIONS.md`/`docs/DEMO-Mx.md`) — this doc formalizes the process that
was already mostly true in practice, it doesn't replace the existing docs.

---

## 1. System of record

**GitHub Issues + PRs, full stop.** No secondary tracker. This is deliberate: it
needs to work identically from a laptop or a phone browser, without a CLI or a
locally-synced database.

Working notes, plans, and design docs still live in the repo (`docs/PLAN.md`,
`docs/DECISIONS.md`, `docs/prd/`, `docs/DEMO-Mx.md`) — those are detail that issues
link to, not a competing tracker. `docs/prd/PRD-M*.md` in particular are TradeDesk's
existing spec format; `/to-spec`/`/to-tickets` publish issues that point back at them
rather than duplicating their content.

## 2. Issue lifecycle

Every issue carries exactly one of these five labels, applied by the `triage` skill
(or by hand). **These labels don't exist on the repo yet** — see
`docs/agents/issue-tracker.md` for the one-time `gh label create` setup.

| Label | Meaning | Applied when |
|---|---|---|
| `needs-triage` | Not yet evaluated | New issue arrives |
| `needs-info` | Waiting on the reporter | Description is incomplete |
| `ready-for-agent` | Fully specified, agent can pick it up | Clear acceptance criteria, no blockers |
| `ready-for-human` | Needs a judgment call | Outside agent scope |
| `wontfix` | Declined | Out of scope |

`ready-for-agent` is the queue the agent team actually pulls from.

## 3. Planning pipeline

1. **Loose idea, size unknown** → `/wayfinder`.
2. **Idea already shaped by a conversation** → `/to-spec`. For TradeDesk, a spec
   issue should point at the relevant `docs/prd/PRD-M*.md` if one exists rather
   than re-deriving its content.
3. **Spec exists** → `/to-tickets`. Slices it into vertical, agent-sized child
   issues.
4. **Any ticket, any time** → `/triage`.

Only `ready-for-agent` tickets are picked up for implementation.

## 4. Branch / PR convention

```
feat/issue-<n>-<slug>
fix/issue-<n>-<slug>
```

Every branch traces back to an issue number. Merge with **real merge commits**
(`gh pr merge --merge`) — matches TradeDesk's existing decision (see
`docs/M7A-BRIEF.md` §4) and repo history.

TradeDesk's existing branches use milestone-numbered (`01-contracts`,
`02-m1-skeleton`, ...) and ad hoc `feat/<slug>` naming. Don't bulk-rename them —
use the issue-driven convention above for new work going forward.

## 5. Agent team shape

`pm` (the human) plus `data-engineer`, `quant`, `frontend`, `qa-reviewer` — see
`docs/agents/roles/` for each role's full mandate. This formalizes the team
structure already described in `docs/PLAN.md` §4's module-ownership table.

Role files are **committed to the repo** — the previous local-only
`.claude/agents/*.md` personas were gitignored and never survived a fresh clone;
`docs/agents/roles/` fixes that.

### Orchestration depth: one level, hard cap

The human drives the `pm` role directly; `pm` spawns dev roles as subagents; dev
roles do not spawn further subagents. Human → PM → dev-role, never deeper — both
for cost (if PM runs on a premium model, nesting multiplies its spend) and
legibility (a human reviewing async needs to reconstruct "who did what" without
three layers of indirection).

If a dev role's task wants to fan out further, that's a signal the ticket wasn't
sliced small enough — split it into a sibling ticket instead.

### PM context hygiene

PM reads ticket bodies, role files, and subagent summaries — not large source
files, raw command output, or full test logs directly. That's a dev role's job,
reported back as a summary. A PM turn that's mostly tool output instead of an
orchestration decision is a sign something should have been delegated.

## 6. Autonomy gate

- **Sub-PRs** — agents may merge autonomously once CI is green and a summary is
  posted. No human wait.
- **Feature-level PRs** (closes a top-level ticket, or crosses a role boundary) —
  always wait for human review before merge. This is TradeDesk's existing Gate 2
  (`docs/M7A-BRIEF.md` §5), generalized to every feature-level PR, not just M7a.

**PR bodies stay reviewable at a glance.** No pasted diffs/logs/transcripts — link
the CI run and `docs/DEMO-Mx.md` instead. One line per review finding.

## 7. Review, layered

1. **`/code-review`** — every PR.
2. **`/interrogate`** — before merging a feature-level PR. This generalizes the
   "critiquer pass" already required by `docs/M7A-BRIEF.md`'s Gate 2.
3. **`thermo-nuclear-code-quality-review`** — a `PreToolUse` hook firing at
   `gh pr create`/`gh pr merge`, both sub-PRs and feature PRs.

## 8. House rules

- **guard-the-context-window**, **never-block-on-the-human**,
  **subtract-before-you-add**, **prove-it-works**, **fix-root-causes** — see
  `CLAUDE.md`'s house-rules section for the full text of each.

TradeDesk's own non-negotiables (read-only broker, mock-first, process-not-outcome
scoring, LLM-as-seam) sit alongside these in `docs/prd/ROADMAP.md` §4 and
`docs/agents/roles/pm.md` — they're project-specific, not part of the shared
template, but equally non-negotiable.

## 9. Skill map

| Layer | Skills | Source |
|---|---|---|
| Planning/tracking | `wayfinder`, `triage`, `to-spec`, `to-tickets`, `handoff` | mattpocock |
| Build | `implement`, `tdd`, `codebase-design`, `domain-modeling` | mattpocock |
| Review | `code-review` + `interrogate`, `thermo-nuclear-code-quality-review` | both |
| Understanding | `how`, `why`, `blast-radius` | ported from `cursor/plugins` (pstack) |
| Process hygiene | `show-me-your-work`, `figure-it-out` | ported from `cursor/plugins` (pstack) |
| Self-tuning | `automate-me` | ported from `cursor/plugins` (pstack) |

Ported skills live at `~/Code/.agents/skills/<name>/`, same mechanism as
mattpocock's installed set.

## 10. What's explicitly out of scope for this template rollout

CI is scoped to backend `pytest -q` (mock mode) + frontend `tsc -b && vite build`
only — no ESLint/ruff/Vitest bundled in yet (flagged as separate follow-up work).
GitHub Actions-based `thermo-nuclear-code-quality-review` (kept local/hook-based
instead — see §7). `arena`, `orchestrate`, the `benny` pack, and the remaining
`principle-*` maxims beyond §8.

## 11. How to verify this doc is working

- A cold read should explain the full lifecycle of an issue from idea to merged PR.
- Run a throwaway issue through `/triage` → `/to-spec` → `/to-tickets` once, confirm
  the issue/PR templates render correctly in GitHub's UI, and confirm CI actually
  runs on a test PR.
- Manually trigger the Stop-hook condition once and confirm the nudge fires and is
  non-blocking.

---

*Vendored from `~/Code/.project-template/WORKFLOW.md`, adapted for TradeDesk's
existing conventions (Gate 1/2, PRD-driven specs, DEMO-Mx.md).*
