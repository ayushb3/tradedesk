# Issue Tracker: GitHub

This project tracks work in **GitHub Issues** on https://github.com/ayushb3/tradedesk.

## How skills interact

Skills like `to-tickets`, `triage`, `to-spec`, and `wayfinder` read/write issues via
the GitHub CLI (`gh`).

### Reading issues
```bash
gh issue list --json title,number,body,labels,state
gh issue view <number> --json title,body,labels,assignees
```

### Creating issues
```bash
gh issue create --title "..." --body "..." --label "label1,label2"
```

### Updating issues
```bash
gh issue edit <number> --title "..." --body "..." --label "label1,label2"
gh issue close <number>
```

### Labeling
Skills apply labels based on the triage vocabulary in `docs/agents/triage-labels.md`.

## Prerequisites

- GitHub CLI installed and authenticated: `gh auth status`
- Remote configured: `git remote -v` includes `origin` pointing at
  `https://github.com/ayushb3/tradedesk.git`

## Setup note (as of this repo's adoption of the shared workflow)

TradeDesk had no GitHub-native scaffolding before this — the five canonical
triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`wontfix`) do not exist on the repo yet and need to be created once:

```bash
gh label create needs-triage    --color EDEDED
gh label create needs-info      --color FBCA04
gh label create ready-for-agent --color 0E8A16
gh label create ready-for-human --color 5319E7
gh label create wontfix         --color CCCCCC
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `gh` command not found | Install the GitHub CLI |
| "Not authenticated" | `gh auth login` |
| "Could not resolve to a repository" | Check `.git/config`'s remote |
| Issue not appearing after create | `gh issue list` may have default filters applied |

---

*Last updated: 2026-07-11*
