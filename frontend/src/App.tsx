import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { ReplayPage } from "@/pages/ReplayPage";
import { JournalPage } from "@/pages/JournalPage";
import { todayET } from "@/lib/format";
// M7a — Trade Review page (share trades) + calendar day drill-in.
import { TradeReviewPage } from "@/pages/TradeReviewPage";
import { JournalDayPage } from "@/pages/JournalDayPage";
// M6 training module — unified scenario/drill list (6.1.2+6.1.3) + authoring + player.
import { ScenarioListPage } from "@/pages/ScenarioListPage";
import { ScenarioAuthorPage } from "@/pages/ScenarioAuthorPage";
import { DrillPlayerPage } from "@/pages/DrillPlayerPage";
// M6 6.3.4 — leaks page (detected behavioral leaks + drill playlist start)
import { LeaksPage } from "@/pages/LeaksPage";
// M6 6.4.2 — progress page (mastery, discipline streaks, due reviews, quests)
import { ProgressPage } from "@/pages/ProgressPage";
// M8a — Knowledge section: course curriculum semantic search
import { KnowledgeSearchPage } from "@/pages/KnowledgeSearchPage";
// M8b — Knowledge: curriculum tree + lesson reader
import { KnowledgeModulesPage } from "@/pages/KnowledgeModulesPage";
import { KnowledgeLessonPage } from "@/pages/KnowledgeLessonPage";
// M8c — Knowledge: concept index + concept detail
import { KnowledgeConceptsPage } from "@/pages/KnowledgeConceptsPage";
import { KnowledgeConceptPage } from "@/pages/KnowledgeConceptPage";

/**
 * Redirect for the retired /debriefs/new/:tradeId page. The debrief interview
 * now lives as a tab on the trade cockpit (#63, closes #62); ?debrief=1 opens
 * that tab. Keeps old links working.
 */
function DebriefRedirect() {
  const { tradeId } = useParams<{ tradeId: string }>();
  return <Navigate to={`/trades/${tradeId}?debrief=1`} replace />;
}

/**
 * Redirect for the retired /daily-review page (#67). The day surface is now the
 * single /journal/:date page (stat row + cards + prompts). Old links land on
 * today's ET session; pick another day from the Journal P&L calendar.
 */
function DailyReviewRedirect() {
  return <Navigate to={`/journal/${todayET()}`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/replay" replace />} />
        <Route path="/replay" element={<ReplayPage />} />
        <Route path="/journal" element={<JournalPage />} />
        {/* M7a — day drill-in + per-trade review (share trades) */}
        <Route path="/journal/:date" element={<JournalDayPage />} />
        <Route path="/trades/:id" element={<TradeReviewPage />} />
        {/* /daily-review retired (#67) — the single day surface is /journal/:date */}
        <Route path="/daily-review" element={<DailyReviewRedirect />} />
        {/* M6 training — unified list (scenarios + drill launch) */}
        <Route path="/training/scenarios" element={<ScenarioListPage />} />
        <Route path="/training/scenarios/new" element={<ScenarioAuthorPage />} />
        {/* /training/drills (lobby) merged into /training/scenarios — redirect for back-compat */}
        <Route
          path="/training/drills"
          element={<Navigate to="/training/scenarios" replace />}
        />
        <Route path="/training/drills/:scenarioId" element={<DrillPlayerPage />} />
        {/* M6 6.3.4 — leaks + prescribed drill playlists */}
        <Route path="/training/leaks" element={<LeaksPage />} />
        {/* M6 6.4.2 — progress: mastery, discipline streaks, due reviews, quests */}
        <Route path="/training/progress" element={<ProgressPage />} />
        {/* Debrief moved onto the cockpit (#63, closes #62): the interview is
            now a tab on /trades/:id. Old links redirect there, opening the
            Debrief tab via ?debrief=1. */}
        <Route path="/debriefs/new/:tradeId" element={<DebriefRedirect />} />
        {/* M8a — Knowledge: course curriculum search (Concepts land in M8c) */}
        <Route path="/knowledge/search" element={<KnowledgeSearchPage />} />
        {/* M8b — Knowledge: curriculum tree + lesson reader (reached from the tree) */}
        <Route path="/knowledge/modules" element={<KnowledgeModulesPage />} />
        <Route path="/knowledge/lessons/:id" element={<KnowledgeLessonPage />} />
        {/* M8c — Knowledge: concept index + concept detail */}
        <Route path="/knowledge/concepts" element={<KnowledgeConceptsPage />} />
        <Route path="/knowledge/concepts/:slug" element={<KnowledgeConceptPage />} />
        <Route path="*" element={<Navigate to="/replay" replace />} />
      </Route>
    </Routes>
  );
}
