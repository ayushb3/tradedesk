import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  api,
  type AgentSessionResponse,
  type AgentSessionsResponse,
  type AgentStatus,
  type AnalyticsSummary,
  type ByDimension,
  type ByRow,
  type CalendarData,
  type DeskDaysResponse,
  type DeskRules,
  type Candle,
  type CandlesParams,
  type CreateSimTradeRequest,
  type CreateTagRequest,
  type CriticResponse,
  type DailyReview,
  type DayTradesResponse,
  type Health,
  type ImportResult,
  type KnowledgeSearchResponse,
  type KnowledgeStatus,
  type KnowledgeConceptsResponse,
  type KnowledgeConceptDetail,
  type KnowledgeLessonResponse,
  type KnowledgeLessonStatus,
  type KnowledgeModulesResponse,
  type KnowledgeProgressResponse,
  type PatchTradeRequest,
  type ReplayContract,
  type ReplayDaySoFar,
  type ReviewDay,
  type ReplayFills,
  type DeskDayDetail,
  type ReplaySession,
  type ReplayTrade,
  type ReplaySessionParams,
  type SimTrade,
  type Tag,
  type Trade,
  type TradeExcursions,
  type TradeReviewResponse,
  type TradesParams,
} from "@/lib/api";

/** TanStack Query hook for a session's candles (M1 raw chart). */
export function useCandles(params: CandlesParams) {
  const { symbol, date, timeframe = "1m" } = params;
  return useQuery<Candle[]>({
    queryKey: ["candles", symbol, date, timeframe],
    queryFn: () => api.candles({ symbol, date, timeframe }),
    enabled: Boolean(symbol && date),
  });
}

/**
 * M2 replay payload: candles + causal indicator/level series + meta.
 * The whole session is fetched once, then revealed progressively client-side.
 * Timeframe + indicator params (EMA lengths, RSI period, OR minutes) are part of
 * the query key, so changing any of them refetches from the backend.
 */
export function useReplaySession(params: ReplaySessionParams) {
  const { symbol, date, timeframe = "1m", emaPeriods, rsiPeriod, orMinutes, enabled = true } = params;
  return useQuery<ReplaySession>({
    queryKey: [
      "replay-session",
      symbol,
      date,
      timeframe,
      emaPeriods?.join(",") ?? "",
      rsiPeriod ?? "",
      orMinutes ?? "",
    ],
    queryFn: () =>
      api.replaySession({ symbol, date, timeframe, emaPeriods, rsiPeriod, orMinutes }),
    enabled: enabled && Boolean(symbol && date),
  });
}

/** 'Replay my trade': the day's session started `lead` minutes before entry. */
export function useReplayTrade(tradeId: number | null, leadMinutes: number) {
  return useQuery<ReplayTrade>({
    queryKey: ["replay-trade", tradeId, leadMinutes],
    queryFn: () => api.replayTrade(tradeId as number, leadMinutes),
    enabled: tradeId !== null,
  });
}

/** Contract mode: the trade's own premium bars + stop line (no fills beyond the start). */
export function useReplayContract(tradeId: number | null, leadMinutes: number, enabled: boolean) {
  return useQuery<ReplayContract>({
    queryKey: ["replay-contract", tradeId, leadMinutes],
    queryFn: () => api.replayTradeContract(tradeId as number, leadMinutes),
    enabled: enabled && tradeId !== null,
  });
}

/**
 * Fills revealed at the playhead. The server only returns fills at or before
 * `playhead`; the UI never filters hidden fills itself.
 */
export function useReplayTradeFills(tradeId: number | null, playhead: string | null) {
  return useQuery<ReplayFills>({
    queryKey: ["replay-trade-fills", tradeId, playhead],
    queryFn: () => api.replayTradeFills(tradeId as number, playhead as string),
    enabled: tradeId !== null && playhead !== null,
    placeholderData: (prev) => prev,
  });
}

/** Log a simulated trade. Returns the server-computed trade (P&L, R). */
export function useCreateSimTrade() {
  return useMutation<SimTrade, unknown, CreateSimTradeRequest>({
    mutationFn: (body) => api.createSimTrade(body),
  });
}

// ---------------------------------------------------------------------------
// M3 — journal / trades / analytics
// ---------------------------------------------------------------------------

/** Query keys that depend on the underlying trade set — refetched after import. */
const JOURNAL_ROOTS = [
  "trades",
  "analytics-summary",
  "analytics-calendar",
  "analytics-by",
  "desk-days",
  "daily-review",
] as const;

/** Import broker fills and (re)build round-trip trades. Invalidates the journal. */
export function useImportFills() {
  const qc = useQueryClient();
  return useMutation<ImportResult, unknown, void>({
    mutationFn: () => api.importFills(),
    onSuccess: () => {
      for (const root of JOURNAL_ROOTS) {
        void qc.invalidateQueries({ queryKey: [root] });
      }
    },
  });
}

/** All built trades, optionally filtered by date range / symbol / tag / sim. */
export function useTrades(params: TradesParams = {}) {
  return useQuery<Trade[]>({
    queryKey: ["trades", params],
    queryFn: () => api.trades(params),
  });
}

/** A single trade with its full enrichment. Enabled only when `id` is set. */
export function useTrade(id: number | string | null) {
  return useQuery<Trade>({
    queryKey: ["trade", id],
    queryFn: () => api.trade(id as number | string),
    enabled: id !== null && id !== undefined,
  });
}

/** PATCH a trade's enrichment. Invalidates the journal so derived stats refresh. */
export function usePatchTrade() {
  const qc = useQueryClient();
  return useMutation<
    Trade,
    unknown,
    { id: number | string; body: PatchTradeRequest }
  >({
    mutationFn: ({ id, body }) => api.patchTrade(id, body),
    onSuccess: (trade) => {
      qc.setQueryData(["trade", trade.id], trade);
      void qc.invalidateQueries({ queryKey: ["trade-review"] });
      for (const root of JOURNAL_ROOTS) {
        void qc.invalidateQueries({ queryKey: [root] });
      }
    },
  });
}

/**
 * One-tap call source (mentor | own) on a trade. After the PUT the review payload, the
 * trade, the excursions and the desk day are refetched so the rule chips and the day
 * tally follow the change.
 */
export function useSetCallSource() {
  const qc = useQueryClient();
  return useMutation<
    Trade,
    unknown,
    { id: number | string; callSource: "mentor" | "own" | null }
  >({
    mutationFn: ({ id, callSource }) => api.setCallSource(id, callSource),
    onSuccess: (trade) => {
      void qc.invalidateQueries({ queryKey: ["trade-review"] });
      void qc.invalidateQueries({ queryKey: ["trade", trade.id] });
      void qc.invalidateQueries({ queryKey: ["review-day"] });
      for (const root of JOURNAL_ROOTS) {
        void qc.invalidateQueries({ queryKey: [root] });
      }
    },
  });
}

/** All tags for the multi-select. */
export function useTags() {
  return useQuery<Tag[]>({
    queryKey: ["tags"],
    queryFn: () => api.tags(),
  });
}

/** Create a tag, then refresh the tag list. */
export function useCreateTag() {
  const qc = useQueryClient();
  return useMutation<Tag, unknown, CreateTagRequest>({
    mutationFn: (body) => api.createTag(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tags"] }),
  });
}

/** Headline analytics for the stat-card row. */
export function useAnalyticsSummary(params: TradesParams = {}) {
  return useQuery<AnalyticsSummary>({
    queryKey: ["analytics-summary", params],
    queryFn: () => api.analyticsSummary(params),
  });
}

/** P&L calendar for a given `YYYY-MM` month. */
export function useAnalyticsCalendar(month: string) {
  return useQuery<CalendarData>({
    queryKey: ["analytics-calendar", month],
    queryFn: () => api.analyticsCalendar(month),
    enabled: Boolean(month),
  });
}

/** Grouped breakdown (by ticker / time-of-day / tag). */
export function useAnalyticsBy(dimension: ByDimension) {
  return useQuery<ByRow[]>({
    queryKey: ["analytics-by", dimension],
    queryFn: () => api.analyticsBy(dimension),
  });
}

/** One session's review payload (trades + summary + prompts). */
export function useDailyReview(date: string) {
  return useQuery<DailyReview>({
    queryKey: ["daily-review", date],
    queryFn: () => api.dailyReview(date),
    enabled: Boolean(date),
  });
}

/** Full review payload for a single trade: trade + fills + underlying descriptor. */
export function useTradeReview(id: number | string | null) {
  return useQuery<TradeReviewResponse>({
    queryKey: ["trade-review", id],
    queryFn: () => api.tradeReview(id as number | string),
    enabled: id !== null && id !== undefined,
  });
}

/** MAE/MFE, stop line and post-exit path for one option trade (#51). */
export function useTradeExcursions(id: number | string | null, enabled = true) {
  return useQuery<TradeExcursions>({
    queryKey: ["trade-excursions", id],
    queryFn: () => api.tradeExcursions(id as number | string),
    enabled: enabled && id !== null && id !== undefined,
  });
}

/** The desk rules (window, stop, target, rule list) for the read-only settings page. */
export function useDeskRules() {
  return useQuery<DeskRules>({
    queryKey: ["desk-rules"],
    queryFn: () => api.deskRules(),
    retry: false,
  });
}

/** Every desk day with its net, rule results and stop state (the Days home). */
export function useDeskDays() {
  return useQuery<DeskDaysResponse>({
    queryKey: ["desk-days"],
    queryFn: () => api.deskDays(),
    retry: false,
  });
}

/** The desk's view of one day: net, stop state, positions with R and rule results. */
export function useDeskDay(date: string | null) {
  return useQuery<DeskDayDetail>({
    queryKey: ["desk-day", date],
    queryFn: () => api.deskDay(date as string),
    enabled: Boolean(date),
    retry: false,
  });
}

/** A session-day's trade cards with sparklines. Enabled only when `date` is set. */
export function useDayTrades(date: string | null) {
  return useQuery<DayTradesResponse>({
    queryKey: ["day-trades", date],
    queryFn: () => api.dayTrades(date as string),
    enabled: Boolean(date),
  });
}

// ---------------------------------------------------------------------------
// M8a — knowledge base search
// ---------------------------------------------------------------------------

/** KB ingestion + embeddings availability (drives the Search view's empty state). */
export function useKnowledgeStatus() {
  return useQuery<KnowledgeStatus>({
    queryKey: ["knowledge-status"],
    queryFn: () => api.knowledgeStatus(),
    staleTime: 60_000,
  });
}

/**
 * Ranked transcript passages for `q`. Disabled until a non-empty query is
 * submitted (the page passes the *submitted* query, not the live input) so we
 * don't fire a request per keystroke.
 */
export function useKnowledgeSearch(q: string, k = 8) {
  return useQuery<KnowledgeSearchResponse>({
    queryKey: ["knowledge-search", q, k],
    queryFn: () => api.knowledgeSearch(q, k),
    enabled: q.trim().length > 0,
  });
}

// ---------------------------------------------------------------------------
// M8b — knowledge base: modules tree + lesson reader + progress
// ---------------------------------------------------------------------------

/** Curriculum tree: modules (ordered) with per-module coverage + lesson rows. */
export function useKnowledgeModules() {
  return useQuery<KnowledgeModulesResponse>({
    queryKey: ["knowledge-modules"],
    queryFn: () => api.knowledgeModules(),
  });
}

/** Full lesson: metadata + raw transcript + chunk anchors + progress + prev/next. */
export function useKnowledgeLesson(id: number | string | null) {
  return useQuery<KnowledgeLessonResponse>({
    queryKey: ["knowledge-lesson", id],
    queryFn: () => api.knowledgeLesson(id as number | string),
    enabled: id !== null && id !== undefined,
  });
}

/**
 * Set a lesson's reading progress (read/starred/unread). Invalidates the
 * modules tree (coverage counts + row status) and that lesson's own query so
 * the reader's toggle reflects immediately.
 */
export function useSetLessonProgress() {
  const qc = useQueryClient();
  return useMutation<
    KnowledgeProgressResponse,
    unknown,
    { id: number | string; status: KnowledgeLessonStatus }
  >({
    mutationFn: ({ id, status }) => api.knowledgeSetLessonProgress(id, status),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ["knowledge-modules"] });
      void qc.invalidateQueries({ queryKey: ["knowledge-lesson", res.lesson_id] });
    },
  });
}

// ---------------------------------------------------------------------------
// M8c — knowledge base: concepts index + concept detail
// ---------------------------------------------------------------------------

/** Concept index (grouped by category on the page). Empty state until the pass runs. */
export function useKnowledgeConcepts() {
  return useQuery<KnowledgeConceptsResponse>({
    queryKey: ["knowledge-concepts"],
    queryFn: () => api.knowledgeConcepts(),
  });
}

/** Concept detail: definition + aliases + related concepts + lessons (by weight desc). */
export function useKnowledgeConcept(slug: string | null | undefined) {
  return useQuery<KnowledgeConceptDetail>({
    queryKey: ["knowledge-concept", slug],
    queryFn: () => api.knowledgeConcept(slug as string),
    enabled: Boolean(slug),
  });
}

/** Backend health/config — exposes the active provider `mode` (mock | webull). */
export function useHealth() {
  return useQuery<Health>({
    queryKey: ["health"],
    queryFn: () => api.health(),
    staleTime: 5 * 60_000, // mode doesn't change within a session
  });
}

// ---------------------------------------------------------------------------
// M9a — Coach agents: status, Trade Critic, session history
// ---------------------------------------------------------------------------

/**
 * Agent-layer availability (drives every Critique affordance). When
 * `available` is false (TRADEDESK_LLM=none) the UI hides/disables the affordance
 * and fires no critic requests (PRD-M9 R1). Cheap + rarely changes → cached.
 */
export function useAgentStatus() {
  return useQuery<AgentStatus>({
    queryKey: ["agent-status"],
    queryFn: () => api.getAgentStatus(),
    staleTime: 5 * 60_000,
  });
}

/**
 * Run the Trade Critic for a trade. Invalidates that trade's session list so a
 * freshly-persisted critique shows up in history (PRD-M9 R4).
 */
export function useRunCritic() {
  const qc = useQueryClient();
  return useMutation<CriticResponse, unknown, number>({
    mutationFn: (tradeId) => api.runCritic(tradeId),
    onSuccess: (_res, tradeId) => {
      void qc.invalidateQueries({
        queryKey: ["agent-sessions", `trade:${tradeId}`],
      });
    },
  });
}

/** Prior critique sessions for a trade (subject_ref `trade:{id}`, kind critic). */
export function useAgentSessionsForTrade(tradeId: number | null) {
  return useQuery<AgentSessionsResponse>({
    queryKey: ["agent-sessions", tradeId != null ? `trade:${tradeId}` : null],
    queryFn: () =>
      api.listAgentSessions({
        subject_ref: `trade:${tradeId}`,
        kind: "critic",
      }),
    enabled: tradeId != null,
  });
}

/** A single agent session with its full message thread. */
export function useAgentSession(id: number | null) {
  return useQuery<AgentSessionResponse>({
    queryKey: ["agent-session", id],
    queryFn: () => api.getAgentSession(id as number),
    enabled: id != null,
  });
}

/**
 * The replayed day before this trade's entry (net so far, stop, target, cutoff,
 * call source), filtered on the server so later trades never reach the browser.
 */
export function useReplayDaySoFar(tradeId: number | null) {
  return useQuery<ReplayDaySoFar>({
    queryKey: ["replay-day-so-far", tradeId],
    queryFn: () => api.replayDaySoFar(tradeId as number),
    enabled: tradeId !== null,
    retry: false,
  });
}

/** The desk day (positions + stop result) behind the Trade review day tally. */
export function useReviewDay(date: string | null) {
  return useQuery<ReviewDay>({
    queryKey: ["review-day", date],
    queryFn: () => api.reviewDay(date as string),
    enabled: Boolean(date),
    retry: false,
  });
}
