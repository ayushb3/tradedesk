import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { ReplayPage } from "@/pages/ReplayPage";
import { JournalPage } from "@/pages/JournalPage";
import { RulesSettingsPage } from "@/pages/RulesSettingsPage";
import { lastTradingDay } from "@/lib/daysHome";
import { practiceHref } from "@/lib/groupReview";
import { trainingRedirect } from "@/lib/learnRoutes";
import { useDeskDays } from "@/lib/queries";
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
// Learn: nightly drills, topic map, lessons
import { LearnPage } from "@/pages/LearnPage";
import { LearnDrillPage } from "@/pages/LearnDrillPage";
import { LearnTopicPage } from "@/pages/LearnTopicPage";
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
 * Redirect for the retired /training/* pages: everything training moved under Learn
 * (/learn, /learn/practice/:id, /learn/leaks, /learn/progress, /learn/scenarios). Keeps old
 * links (and any query string such as ?playlist) working.
 */
function TrainingRedirect() {
  const { pathname, search } = useLocation();
  return <Navigate to={trainingRedirect(pathname, search)} replace />;
}

/**
 * Redirect for the retired /daily-review page: Daily Review is merged into
 * Days, so old links land on the last trading day's Day page (or the Days home
 * when there are no trades yet). Waits for the day list, then replaces.
 */
function DailyReviewRedirect() {
  const days = useDeskDays();
  if (days.isLoading) return null;
  const last = lastTradingDay(days.data?.days ?? []);
  return <Navigate to={last ? `/days/${last}` : "/days"} replace />;
}

/**
 * Practice (top bar): free replay on the last trading day, at the money. Waits for the day
 * list; with no trades yet the player picks the previous weekday itself.
 */
function PracticeRedirect() {
  const days = useDeskDays();
  if (days.isLoading) return null;
  const last = lastTradingDay(days.data?.days ?? []);
  return <Navigate to={last ? practiceHref(last) : "/replay?mode=symbol"} replace />;
}

/**
 * Redirect for the retired /journal URLs: the calendar is /days and a day is /days/:date
 * ("journal" read as the whole app). Keeps old links, query string included, working.
 */
function JournalRedirect() {
  const { pathname, search } = useLocation();
  return <Navigate to={`${pathname.replace(/^\/journal/, "/days")}${search}`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        {/* The app opens on Days (the home shows the last trading day's month). */}
        <Route index element={<Navigate to="/days" replace />} />
        {/* Replay is an action on a trade or a day (?trade= / ?day=), not a page:
            bare /replay redirects to the Days calendar. */}
        <Route path="/replay" element={<ReplayPage />} />
        <Route path="/days" element={<JournalPage />} />
        <Route path="/practice" element={<PracticeRedirect />} />
        {/* M7a — day drill-in + per-trade review (share trades) */}
        <Route path="/days/:date" element={<JournalDayPage />} />
        {/* The calendar used to live at /journal; old links redirect. */}
        <Route path="/journal/*" element={<JournalRedirect />} />
        <Route path="/trades/:id" element={<TradeReviewPage />} />
        {/* Daily Review is merged into Days: old route goes to the last trading day */}
        <Route path="/daily-review" element={<DailyReviewRedirect />} />
        {/* The old training pages live under Learn now; their URLs redirect. */}
        <Route path="/training/*" element={<TrainingRedirect />} />
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
        {/* Learn, the one home: today's drills, drills from your trades, progress, lessons */}
        <Route path="/learn" element={<LearnPage />} />
        <Route path="/learn/drills/:id" element={<LearnDrillPage />} />
        <Route path="/learn/topics/:slug" element={<LearnTopicPage />} />
        {/* Scenario drills (Critique / Debrief / leak playlists), leaks and progress */}
        <Route path="/learn/practice/:scenarioId" element={<DrillPlayerPage />} />
        <Route path="/learn/scenarios" element={<ScenarioListPage />} />
        <Route path="/learn/scenarios/new" element={<ScenarioAuthorPage />} />
        <Route path="/learn/leaks" element={<LeaksPage />} />
        <Route path="/learn/progress" element={<ProgressPage />} />
        {/* Rules: a small settings page, reached from the gear in the nav footer */}
        <Route path="/settings/rules" element={<RulesSettingsPage />} />
        <Route path="*" element={<Navigate to="/days" replace />} />
      </Route>
    </Routes>
  );
}
