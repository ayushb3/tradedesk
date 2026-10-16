# DEMO — #63 (closes #62): Trade Debrief onto the cockpit

**Goal:** the Trade Debrief interview was a standalone, chartless page at
`/debriefs/new/:tradeId`. Answering it stranded the user away from the annotated
chart they were reviewing, and its "back/close" affordances left them nowhere
useful (#62). The debrief interview now lives as a **tab on the trade cockpit**
(`/trades/:id`), so the annotated chart stays visible in the left column while
the user answers the interview in the right column.

Scope: `frontend/` only. Epic #64, workstream WS-B. Issue #63, closes #62.
The debrief API contract is unchanged — this is a UI relocation, not a rewrite.

## What lands

- **Debrief is now a tab in the cockpit's right column.** The right `<aside>` on
  `TradeReviewPage` (previously a single "Journal" heading over
  `<TradeEnrichmentPanel>`) is now a two-tab panel: **`Journal | Debrief`**. The
  right column widened from `340px` → `400px` (both the content grid and the
  loading-skeleton grid) to give the interview room.
- **The interview UI is extracted into a reusable component**,
  `frontend/src/components/review/DebriefPanel.tsx`. It takes `tradeId: number`
  and an optional `onGetCritique` callback. The `useReducer` state machine
  (starting → active → previewing → saved → error) and all turn-rendering logic
  (chat bubbles, stepper cards, preview, saved summary) were moved as-is from the
  old page — a **move, not a rewrite**. The panel's own header/footer chrome was
  dropped (it no longer owns a full-page layout); it fits inside the tab.
- **`/debriefs/new/:tradeId` redirects to `/trades/:id?debrief=1`.** Old links
  keep working. `?debrief=1` opens the cockpit on the Debrief tab
  (`TradeReviewPage` reads it via `useSearchParams`). The standalone
  `DebriefPage` file and its route element are deleted; the route now renders a
  small `DebriefRedirect` in `App.tsx`.
- **"Get critique" no longer navigates.** The Trade Critic already renders in the
  cockpit's LEFT column. From the debrief's saved screen, "Get critique" now
  triggers the in-page critique (if not already run) and scrolls the critique
  section into view — no route change. Gated on agent availability, same as the
  header "Critique" button. The former terminal navigations that stranded the
  user (`→ /journal`, `→ /trades/:id`) are gone; the cockpit's persistent header
  (back + prev/next) is the escape hatch, resolving #62 structurally.
- **"Save & make a drill" still navigates** to `/training/drills/:scenario_id` —
  that cross-section jump is intended and preserved.
- **TradeDetail entry points repointed.** The two "Debrief this trade" /
  "continue debrief" navigations in the journal drawer now go to
  `/trades/:id?debrief=1` instead of the retired route. (TradeDetail itself is
  removed by a later workstream, WS-C / #67 — untouched here beyond these two
  navigations.)

## Tabs approach

Used a **controlled button-toggle** (two `<Button>`s, `variant` flips between
`default` and `outline` on the active tab), not a new Radix Tabs primitive.
Reasons: no `@radix-ui/react-tabs` dependency exists in the project, no
`components/ui/tabs.tsx` exists, and the codebase already expresses this exact
pattern elsewhere (e.g. `KnowledgeLessonPage`'s Read/Star toggle uses
`variant={active ? "default" : "outline"}`). This keeps the change dependency-free
and visually consistent with the existing dark-theme shadcn look.

## Before / After

| | Before | After |
|---|---|---|
| Debrief location | standalone chartless page `/debriefs/new/:tradeId` | tab on the cockpit `/trades/:id`, chart stays visible |
| Right column width | `340px` | `400px` |
| Old debrief URL | rendered `DebriefPage` | redirects to `/trades/:id?debrief=1` |
| "Get critique" | `navigate(/trades/:id)` (route change) | triggers + scrolls to the in-cockpit critique, no navigation |
| Escaping the debrief | terminal "back to journal" buttons (#62 stranding) | cockpit's persistent header (back + prev/next) |

## Verify (click-path)

```bash
# .env: TRADEDESK_MODE=mock
make dev            # backend :9000 + frontend :5173
```

1. Open **`/trades/135?debrief=1`**. The cockpit loads with the SPXW chart +
   fills in the left column and the **Debrief tab active** in the right column,
   showing the first interview step. The chart never leaves the screen.
2. Click the **`Journal`** tab → the enrichment panel shows; click **`Debrief`**
   → back to the interview. The chart stays put throughout.
3. Open **`/debriefs/new/135`** → URL redirects to **`/trades/135?debrief=1`**
   and lands on the Debrief tab. (Old links still work.)
4. From the debrief's saved screen, with an agent backend available, **Get
   critique** runs the critique in the left column and scrolls to it — no route
   change. **Save & make a drill** still navigates to the drill player.

## Live-run evidence (this session)

Mock mode, `/trades/135?debrief=1`: right column defaults to the **Debrief** tab
(`bg-primary` on the Debrief button, `outline` on Journal), the SPXW underlying +
premium charts render in the left column with entry/exit + MFE/MAE markers, and
the interview's stepper card renders in the right column. Toggling
`Journal ↔ Debrief` works. `/debriefs/new/135` redirects to
`/trades/135?debrief=1` and lands on the Debrief tab. Only console error is a
favicon 404 from the playwright harness itself (`localhost:8765/favicon.ico`),
not the app. `npm run build` (tsc + vite) green.

## Screenshots

- `.agents/screenshots/debrief-tab-cockpit.png` — full page, Debrief tab active:
  underlying chart + fills in the left column, interview in the right.
- `.agents/screenshots/debrief-tab-header.png` — top of the cockpit showing the
  `Journal | Debrief` toggle and the premium chart with markers.

## Files changed

- **Added** `frontend/src/components/review/DebriefPanel.tsx` — extracted,
  reusable interview panel (reducer + turn rendering moved from DebriefPage).
- **Changed** `frontend/src/pages/TradeReviewPage.tsx` — right column is now a
  `Journal | Debrief` tab panel; `useSearchParams` reads `?debrief=1`; grids
  widened `340px → 400px`; `focusCritique` triggers + scrolls to the in-page
  critique for the debrief's "Get critique".
- **Changed** `frontend/src/App.tsx` — dropped `DebriefPage` import/route; added
  `DebriefRedirect` (`/debriefs/new/:tradeId` → `/trades/:id?debrief=1`).
- **Changed** `frontend/src/components/journal/TradeDetail.tsx` — two debrief
  navigations repointed to `/trades/:id?debrief=1`.
- **Deleted** `frontend/src/pages/DebriefPage.tsx` — logic lives in DebriefPanel;
  route is now a redirect.

## Manual test plan (for reviewer, real env)

```bash
git checkout feat/issue-63-debrief-onto-cockpit
cd frontend && npm run build            # green (tsc + vite)
# .env: TRADEDESK_MODE=mock
make dev
#   → /trades/<id>?debrief=1 : Debrief tab active, chart visible in left column
#   → click Journal / Debrief : toggle works, chart stays put
#   → /debriefs/new/<id>      : redirects to /trades/<id>?debrief=1, Debrief tab
#   → (agent backend) finish a debrief → "Get critique" runs it in-place,
#       scrolls to it, no navigation; "Save & make a drill" → drill player
```
