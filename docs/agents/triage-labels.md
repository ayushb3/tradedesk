# Triage Label Vocabulary

Canonical label names used by the `triage`, `to-tickets`, and `to-spec` skills. When
these skills process or create issues, they apply these labels — this mapping is
fixed across every project on this template, not per-project configuration.

## The five canonical states

| Label | Meaning | Applied when |
|---|---|---|
| `needs-triage` | Not yet evaluated | New issue arrives; not yet classified |
| `needs-info` | Waiting on the reporter | Description is incomplete; needs clarification |
| `ready-for-agent` | Fully specified, agent-ready | Clear acceptance criteria, no blockers |
| `ready-for-human` | Needs human implementation | Requires a judgment call outside agent scope |
| `wontfix` | Will not be actioned | Out of scope or explicitly declined |

Use the default label names exactly as above. If a project needs to rename one
(e.g. `needs-triage` → `bug:triage`), that's a deliberate per-project divergence —
document it at the top of the project's copy of this file, don't change the
meaning of the state.

## Additional labels (informational, project may extend)

Beyond the five canonical states, use GitHub's built-in category labels for
organization. These don't affect skill workflows:

- `type:bug` / `type:feature` / `type:enhancement` / `type:docs` / `type:chore`
- `priority:p0` (critical) / `priority:p1` (high) / `priority:p2` (medium) / `priority:p3` (backlog)

---

*Doc-of-record copy lives in `.project-template/docs/agents/triage-labels.md` —
project copies should stay in sync unless a divergence is deliberate and noted.*
