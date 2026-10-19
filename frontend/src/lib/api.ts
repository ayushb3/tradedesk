/**
 * Typed client for the frozen TradeDesk backend contract.
 * Backend runs at http://localhost:9000; in dev the Vite proxy maps
 * /api/* -> http://localhost:9000/api/* (see vite.config.ts).
 *
 * Do NOT mock responses here — always integrate against the real API.
 */

/** Health payload: GET /api/health */
export interface Health {
  status: string;
  mode: string;
  tz: string;
}

/**
 * A single OHLCV bar. Mirrors backend `Candle` (app/models/market.py).
 * `ts` is the bar OPEN time as an ISO 8601 UTC string.
 */
export interface Candle {
  symbol: string;
  ts: string; // ISO 8601 UTC, bar open time
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  timeframe: string;
  /** Set when the bars are a stand-in, e.g. "SPY (proxy)". */
  source_label?: string | null;
}

export type Timeframe = "1m" | "5m" | "15m" | "30m" | "1h" | "1d";

export interface CandlesParams {
  symbol: string;
  /** Session date in YYYY-MM-DD. */
  date: string;
  timeframe?: Timeframe;
}

const API_BASE = "/api";

/** Thrown for any non-2xx response or network failure hitting the backend. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      ...init,
    });
  } catch (cause) {
    // Network-level failure — most commonly the backend isn't running.
    throw new ApiError(
      "Could not reach the TradeDesk backend. Is it running on :9000?",
      0,
      cause instanceof Error ? cause.message : String(cause),
    );
  }

  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = (await res.json()) as { detail?: string };
      detail = body?.detail;
    } catch {
      // response wasn't JSON; ignore
    }
    throw new ApiError(
      `Request to ${path} failed (${res.status})`,
      res.status,
      detail,
    );
  }

  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// M2 — replay session contract: GET /api/replay/session
// ---------------------------------------------------------------------------

/** Causal indicator series. Every array is aligned 1:1 with `candles`. */
export type IndicatorKey =
  | "ema9"
  | "ema21"
  | "ema50"
  | "ema200"
  | "vwap"
  | "rsi14";

/** Causal level series. Every array is aligned 1:1 with `candles`. */
export type LevelKey =
  | "pdh"
  | "pdl"
  | "pmh"
  | "pml"
  | "day_high"
  | "day_low"
  | "or_high"
  | "or_low";

/**
 * A numeric series aligned to candles. `series[i]` is the CAUSAL value valid
 * at bar `i` — computed from bars <= i only. May be null (e.g. before an EMA
 * has warmed up, or before the opening range is defined). Read via index; never
 * read `series[j]` for j > currentIndex during replay (that leaks the future).
 */
export type AlignedSeries = (number | null)[];

// Partial because the backend may omit a key entirely; consumers must guard.
export type IndicatorSeriesMap = Partial<Record<IndicatorKey, AlignedSeries>>;
export type LevelSeriesMap = Partial<Record<LevelKey, AlignedSeries>>;

export interface ReplaySessionMeta {
  or_minutes: number;
  tz: string;
  /** Present when the provider serves a stand-in, e.g. "SPY (proxy)". */
  source_label?: string;
}

/** A trade fill revealed by the playhead. Never carries P&L. */
export interface ReplayFill {
  seq: number;
  role: "entry" | "exit";
  side: "buy" | "sell";
  qty: number;
  price: number;
  ts: string;
  /** Display-zone time, already labelled, e.g. "07:30:00 PT". */
  time_pt: string;
}

/** GET /api/replay/trade/{id} - 'replay my trade'. */
export interface ReplayTrade {
  trade_id: number;
  symbol: string;
  lead_minutes: number;
  start_ts: string;
  start_index: number;
  /** Exit + 30 min, only once the playhead is at or past the exit. */
  window_end_ts: string | null;
  playhead: string;
  session: ReplaySession;
  fills: ReplayFill[];
}

/** GET /api/replay/trade/{id}/contract - Contract mode: the trade's own premium bars. */
export interface ReplayContract {
  trade_id: number;
  available: boolean;
  reason: string | null;
  symbol: string | null;
  session_date: string | null;
  lead_minutes: number;
  start_ts: string;
  start_index: number;
  window_end_ts: string | null;
  playhead: string;
  candles: Candle[];
  /** Premium level at which the trade's risk is lost (same stop as the review). */
  stop_price: number | null;
  /** True when no max loss was written: `stop_price` is the default (-30%) stop. */
  stop_assumed?: boolean;
  synthetic: boolean;
  /** Bars are a model, not provider prints: show the "estimated" badge. */
  estimated: boolean;
  method: string | null;
  fills: ReplayFill[];
}

export interface ReplayFills {
  trade_id: number;
  playhead: string;
  fills: ReplayFill[];
  /** Exit + 30 min; present only once the playhead is at or past the exit. */
  window_end_ts?: string | null;
}

/** Full replay payload: GET /api/replay/session?symbol=&date= */
export interface ReplaySession {
  symbol: string;
  session_date: string;
  candles: Candle[];
  indicators: IndicatorSeriesMap;
  levels: LevelSeriesMap;
  meta: ReplaySessionMeta;
}

export interface ReplaySessionParams {
  symbol: string;
  /** Session date in YYYY-MM-DD. */
  date: string;
  /** Candle timeframe; backend re-fetches + recomputes indicators. Default 1m. */
  timeframe?: ReplayTimeframe;
  /** EMA lengths to compute; backend keys them ema<period>. Default [9,21,50,200]. */
  emaPeriods?: number[];
  /** RSI length; backend keys it rsi<period>. Default 14. */
  rsiPeriod?: number;
  /** Opening-range window in minutes for OR levels. Default from settings (5). */
  orMinutes?: number;
  /** Set false to skip the fetch (e.g. in trade-replay mode). Default true. */
  enabled?: boolean;
}

/** Timeframes the replay endpoint supports (mirrors backend _SUPPORTED_TIMEFRAMES). */
export type ReplayTimeframe = "1m" | "5m" | "15m" | "1h" | "1d";
export const REPLAY_TIMEFRAMES: readonly ReplayTimeframe[] = [
  "1m",
  "5m",
  "15m",
  "1h",
  "1d",
] as const;

// ---------------------------------------------------------------------------
// M2 — simulated trades: POST /api/sim/trades
// ---------------------------------------------------------------------------

export type TradeDirection = "long" | "short";

/** A single fill leg (entry or exit). `ts` is ISO 8601 UTC. */
export interface SimTradeLeg {
  ts: string;
  qty: number;
  price: number;
}

export interface CreateSimTradeRequest {
  symbol: string;
  direction: TradeDirection;
  session_date: string;
  entries: SimTradeLeg[];
  exits: SimTradeLeg[];
  stop_price?: number;
  notes?: string;
}

/**
 * Trade returned by the backend after logging. The exact result field names
 * aren't frozen yet, so P&L / R are read defensively (see `netPnl` / `rMultiple`
 * helpers). Everything derived is optional + nullable.
 */
export interface SimTrade {
  id?: string | number;
  symbol: string;
  direction: TradeDirection;
  session_date: string;
  entries: SimTradeLeg[];
  exits: SimTradeLeg[];
  stop_price?: number | null;
  notes?: string | null;
  // Result fields — accept a couple of plausible names, all optional.
  net_pnl?: number | null;
  pnl?: number | null;
  r?: number | null;
  r_multiple?: number | null;
}

/** Net P&L from a returned trade, whichever field the backend used. */
export function netPnl(t: SimTrade): number | null {
  return t.net_pnl ?? t.pnl ?? null;
}

/** R multiple from a returned trade, whichever field the backend used. */
export function rMultiple(t: SimTrade): number | null {
  return t.r ?? t.r_multiple ?? null;
}

// ---------------------------------------------------------------------------
// M3 — journal / trades / analytics contract
// ---------------------------------------------------------------------------

export type TradeGrade = "A" | "B" | "C";
export type TradeStatus = "open" | "closed";

/** A user-defined tag. Extra backend fields are tolerated but unused here. */
export interface Tag {
  id: number;
  name: string;
  description?: string | null;
  color?: string | null;
}

/** One row of a trade's rule checklist. */
export interface ChecklistItem {
  item: string;
  checked: boolean;
}

/** Qualitative enrichment layered on top of the mechanical trade record. */
export interface TradeEnrichment {
  notes: string | null;
  grade: TradeGrade | null;
  checklist: ChecklistItem[];
  screenshots: string[];
  /** One-tap whose idea; null = not recorded. Optional so older payloads still type. */
  call_source?: "mentor" | "own" | null;
}

/** One rule's verdict: kept true / broken false / unknown null. */
export interface RuleResult {
  code:
    | "before_cutoff"
    | "daily_stop"
    | "max_loss_written"
    | "call_source"
    | "size_within_plan";
  kept: boolean | null;
  detail: string;
}

/**
 * A built round-trip trade. Mechanical fields (pnl, R, hold time) are computed
 * by the backend; `enrichment` + `tags` are the user-editable journal layer.
 * Contract guarantees `enrichment`/`tags`, but the UI still reads defensively.
 */
export interface Trade {
  id: number;
  symbol: string;
  contract_label?: string | null;
  direction: TradeDirection;
  open_ts: string;
  close_ts: string | null;
  session_date: string;
  qty: number;
  avg_entry: number;
  avg_exit: number | null;
  gross_pnl: number;
  fees: number;
  net_pnl: number;
  pnl_pct: number;
  stop_price: number | null;
  r_multiple: number | null;
  /** "written" = measured on the written max loss; "assumed" = 30% of cost. */
  r_basis?: "written" | "assumed" | null;
  rule_results?: RuleResult[];
  hold_time_s: number | null;
  status: TradeStatus;
  simulated: boolean;
  tags: Tag[];
  enrichment: TradeEnrichment;
}

export interface TradesParams {
  from?: string;
  to?: string;
  symbol?: string;
  tag?: string;
  simulated?: boolean;
}

/** Body for PATCH /api/trades/{id}. Every field is an optional partial update. */
export interface PatchTradeRequest {
  notes?: string;
  grade?: TradeGrade;
  stop_price?: number;
  tag_ids?: number[];
  checklist?: ChecklistItem[];
  screenshots?: string[];
}

export interface ImportResult {
  fills_imported: number;
  trades_built: number;
}

export interface CreateTagRequest {
  name: string;
  description?: string;
}

/** GET /api/analytics/summary — headline journal stats. */
export interface AnalyticsSummary {
  trade_count: number;
  win_rate: number;
  profit_factor: number;
  avg_r: number;
  expectancy: number;
  total_pnl: number;
  avg_win: number;
  avg_loss: number;
  avg_hold_time_s: number;
}

/** One day cell in GET /api/analytics/calendar (keyed by YYYY-MM-DD). */
export interface CalendarDay {
  pnl: number;
  trades: number;
  wins?: number;
  losses?: number;
}

export type CalendarData = Record<string, CalendarDay>;

export type ByDimension = "ticker" | "tod" | "tag";

/** One grouped row from GET /api/analytics/by. */
export interface ByRow {
  key: string;
  trade_count: number;
  pnl: number;
  win_rate: number;
  avg_r: number;
}

/**
 * GET /api/daily-review — one session's trades, rollup stats, and reflection
 * prompts. `summary` mirrors the analytics summary but may be partial per day.
 */
export interface DailyReview {
  date: string;
  trades: Trade[];
  summary: Partial<AnalyticsSummary>;
  prompts: string[];
}

// ---------------------------------------------------------------------------
// M7a — trade review + calendar day drill-in
// ---------------------------------------------------------------------------

/** Which side of the position a fill belongs to (derived server-side). */
export type FillRole = "entry" | "exit";
export type FillSide = "buy" | "sell";

/**
 * One executed fill of a trade. Ordered by `seq` (FIFO membership order) both
 * in the chart markers and the fill table — they read the same array. `ts` is
 * ISO 8601 UTC.
 */
export interface ReviewFill {
  id: number;
  seq: number;
  side: FillSide;
  qty: number;
  price: number;
  ts: string;
  fees: number;
  role: FillRole;
}

/** Ticker + timeframe descriptor for the underlying chart. */
export interface ReviewUnderlying {
  symbol: string;
  session_date: string;
  timeframes: Timeframe[];
}

/**
 * Option contract descriptor. `null` for share trades; populated (M7b) for
 * option trades. The frontend renders "underlying chart only" when
 * `option === null`, and adds the premium chart when it's present.
 */
export interface ReviewOption {
  contract_key: string;
  descriptor: string;
  option_type: string;
  strike: number;
  expiry: string;
  synthetic: boolean;
  /**
   * Provenance of `premium_bars`: "provider" (provider-supplied) or
   * "bs_interp" (server-side Black-Scholes fallback anchored to fills). `null`
   * only in the legacy stub case. `bs_interp` ⇒ estimated premium.
   */
  method: "provider" | "bs_interp" | null;
}

/**
 * Max favorable / adverse excursion over the hold window (PRD-M7 R9). Computed
 * from the premium series for option trades (`basis: "premium"`) or the
 * underlying for share trades (`basis: "underlying"`). `mfe`/`mae` are price
 * points (always ≥ 0); `mfe_pct`/`mae_pct` are % of entry price. `estimated`
 * is true when the source premium series is synthetic (BS fallback).
 */
export interface Excursion {
  mfe: number;
  mae: number;
  mfe_pct: number;
  mae_pct: number;
  basis: "premium" | "underlying";
  estimated: boolean;
}

/** GET /api/trades/{id}/review — full review payload. */
export interface TradeReviewResponse {
  trade: Trade;
  fills: ReviewFill[];
  underlying: ReviewUnderlying;
  /** null for share trades; populated for option trades (M7b). */
  option: ReviewOption | null;
  /** null for share trades; option premium bars (1m) for option trades. */
  premium_bars: Candle[] | null;
  /** MFE/MAE over the hold window; present for shares and options. */
  excursion: Excursion | null;
}

/** GET /api/trades/{id}/excursions — MAE/MFE, stop line and post-exit path (#51). */
export interface ExcursionFill {
  side: "B" | "S";
  qty: number;
  px: number;
  ts: string;
  ts_pt: string;
}

export interface HoldExcursion {
  /** Signed: MAE <= 0, MFE >= 0. Dollars and R on the position's average cost. */
  mae_usd: number;
  mfe_usd: number;
  mae_r: number | null;
  mfe_r: number | null;
  mae_ts_pt: string | null;
  mfe_ts_pt: string | null;
  drawdown_minutes: number;
  crossed_stop: boolean;
  crossed_stop_at_pt: string | null;
}

export interface PostExitWindow {
  minutes: number;
  best_r: number | null;
  worst_r: number | null;
  bars: number;
  complete: boolean;
}

export interface TradeExcursions {
  trade_id: number;
  tz_label: string;
  available: boolean;
  reason: string | null;
  contract: string | null;
  estimated: boolean;
  status: "open" | "closed";
  avg_in: number | null;
  qty: number;
  risk: number | null;
  risk_source: "written_max_loss" | "default_risk_pct";
  stop_price: number | null;
  fills: ExcursionFill[];
  hold: HoldExcursion | null;
  post_exit: PostExitWindow[];
}

/** GET /api/trades/{id}/option-bars — standalone premium OHLCV series. */
export interface OptionBarsResponse {
  bars: Candle[];
  synthetic: boolean;
  method: "provider" | "bs_interp";
}

/**
 * Downsampled underlying close prices over a trade's hold window. `values` are
 * floats (never null inside); the whole object is null when the window yields
 * no candles. `t0`/`t1` are the ISO timestamps of the first/last value.
 */
export interface Sparkline {
  symbol: string;
  timeframe: string;
  values: number[];
  t0: string;
  t1: string;
}

/** A thin projection of a Trade for scanning a day, plus a sparkline. */
export interface DayTradeCard {
  id: number;
  symbol: string;
  contract_label?: string | null;
  direction: TradeDirection;
  net_pnl: number;
  pnl_pct: number;
  r_multiple: number | null;
  hold_time_s: number | null;
  grade: TradeGrade | null;
  open_ts: string;
  close_ts: string | null;
  simulated: boolean;
  sparkline: Sparkline | null;
}

/** GET /api/journal/days/{date}/trades — that session-day's trade cards. */
export interface DayTradesResponse {
  date: string;
  trades: DayTradeCard[];
}

// ---------------------------------------------------------------------------
// M8a — knowledge base: search
// ---------------------------------------------------------------------------

/** One ranked transcript passage returned by GET /api/knowledge/search. */
export interface KnowledgeSearchHit {
  text: string;
  score: number;
  lesson_id: number;
  lesson_title: string;
  module: string;
  seq: number;
  char_start: number;
  char_end: number;
  source_url: string | null;
  /** e.g. "Module 1 · Lesson 003 — Example Lesson". */
  citation: string;
}

/**
 * GET /api/knowledge/search response. When `available` is false the KB hasn't been
 * ingested (or embeddings are unavailable) and `results` is empty; `detail`
 * carries user-facing guidance.
 */
export interface KnowledgeSearchResponse {
  query: string;
  k: number;
  available: boolean;
  status: string;
  detail: string | null;
  embedder: string | null;
  results: KnowledgeSearchHit[];
}

/** GET /api/knowledge/status — ingestion + embeddings availability. */
export interface KnowledgeStatus {
  ingested: boolean;
  lesson_count: number;
  module_counts: Record<string, number>;
  embedder: string;
  local_embeddings_available: boolean;
  chromadb_available: boolean;
}

// ---------------------------------------------------------------------------
// M8b — knowledge base: modules tree + lesson reader + progress
// ---------------------------------------------------------------------------

/** A lesson's reading progress state, from `knowledge_lesson_progress`. */
export type KnowledgeLessonStatus = "unread" | "read" | "starred";

/** One lesson row inside a module (curriculum tree). */
export interface KnowledgeModuleLesson {
  id: number;
  seq: number;
  title: string;
  module: string;
  status: KnowledgeLessonStatus;
  source_url: string | null;
  /** e.g. "Module 1 · Lesson 003 — Example Lesson". */
  citation: string;
}

/** One module node in the curriculum tree, with coverage counts. */
export interface KnowledgeModule {
  module: string;
  label: string;
  lesson_count: number;
  /** Lessons whose status is `read` OR `starred` (starred implies engaged). */
  read_count: number;
  /** Lessons whose status is `starred`. */
  starred_count: number;
  lessons: KnowledgeModuleLesson[];
}

/**
 * GET /api/knowledge/modules response. When `available` is false the KB hasn't been
 * ingested; `modules` is empty and `detail` carries user-facing guidance.
 */
export interface KnowledgeModulesResponse {
  available: boolean;
  status: string;
  detail: string | null;
  lesson_count: number;
  module_count: number;
  modules: KnowledgeModule[];
}

/** Char-offset anchor for a transcript chunk (deep-linkable reader sections). */
export interface KnowledgeChunkAnchor {
  seq: number;
  char_start: number;
  char_end: number;
}

/**
 * One self-check quiz question on a study sheet. `answer_idx` is a validated
 * 0-based index into `choices`. `explanation` may be null. Framed as a
 * self-check, never a graded test (no scoring persisted).
 */
export interface KnowledgeQuizQuestion {
  q: string;
  choices: string[];
  answer_idx: number;
  explanation: string | null;
}

/**
 * A generated study sheet for a lesson (M8c PR-2). `summary_md` / `rules_md`
 * are lightweight markdown (rendered whitespace-preserved). `diagram_mermaid`
 * is Mermaid source or null. `quiz` is always an array (may be empty).
 * `generated_at` is an ISO datetime when persisted (else null).
 */
export interface KnowledgeLessonSheet {
  summary_md: string;
  rules_md: string;
  diagram_mermaid: string | null;
  quiz: KnowledgeQuizQuestion[];
  generated_at: string | null;
  model: string;
}

/** Full lesson payload: metadata + raw transcript + progress + chunk anchors. */
export interface KnowledgeLessonResponse {
  available: boolean;
  status: string;
  detail: string | null;
  id: number;
  module: string;
  seq: number;
  title: string;
  citation: string;
  source_url: string | null;
  raw_text: string;
  chunk_count: number;
  chunks: KnowledgeChunkAnchor[];
  progress: { status: KnowledgeLessonStatus; updated_at: string | null };
  /** Generated study sheet (M8c PR-2); null when no sheet exists for the lesson. */
  sheet: KnowledgeLessonSheet | null;
  /** Concepts this lesson teaches, ordered by weight desc. Empty until the concepts pass runs. */
  concepts: KnowledgeLessonConceptRef[];
  prev_lesson_id: number | null;
  next_lesson_id: number | null;
}

/** PUT /api/knowledge/lessons/{id}/progress response. */
export interface KnowledgeProgressResponse {
  lesson_id: number;
  status: KnowledgeLessonStatus;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// M8c — knowledge base: concepts index + concept detail
// ---------------------------------------------------------------------------

/** Concept categories from the seed taxonomy (raw grouping keys). */
export type KnowledgeConceptCategory =
  | "Strategy"
  | "Risk"
  | "Psychology"
  | "Process";

/** How one concept relates to another (edge label on `related`). */
export type KnowledgeConceptRelation = "related" | "prerequisite" | "contrast" | "part_of";

/** One concept card in the index (GET /api/knowledge/concepts). */
export interface KnowledgeConceptSummary {
  slug: string;
  name: string;
  category: string;
  definition: string;
  aliases: string[];
  lesson_count: number;
}

/**
 * GET /api/knowledge/concepts response. When `available` is false the concepts pass
 * hasn't been run; `concepts` is empty and `detail` carries user-facing guidance
 * (run `make knowledge-concepts`).
 */
export interface KnowledgeConceptsResponse {
  available: boolean;
  status: string;
  detail: string | null;
  concept_count: number;
  concepts: KnowledgeConceptSummary[];
}

/** A lesson that teaches a concept (on the concept detail payload). */
export interface KnowledgeConceptLessonRef {
  lesson_id: number;
  module: string;
  seq: number;
  title: string;
  weight: number;
  citation: string;
}

/** A neighbouring concept, with the relation edge label. */
export interface KnowledgeRelatedConcept {
  slug: string;
  name: string;
  relation: KnowledgeConceptRelation;
}

/** GET /api/knowledge/concepts/{slug} — concept detail (404 if the slug is unknown). */
export interface KnowledgeConceptDetail {
  slug: string;
  name: string;
  category: string;
  definition: string;
  aliases: string[];
  lesson_count: number;
  lessons: KnowledgeConceptLessonRef[];
  related: KnowledgeRelatedConcept[];
}

/** A concept reference on a lesson payload (ordered by weight desc). */
export interface KnowledgeLessonConceptRef {
  slug: string;
  name: string;
  category: string;
  weight: number;
}

// ---------------------------------------------------------------------------
// M8c/M9a — course retrieval composer: POST /api/knowledge/retrieve
// ---------------------------------------------------------------------------

/**
 * POST /api/knowledge/retrieve response (the agent-facing retrieval entry point).
 * Composes semantic search with concept expansion — top-k passages plus the
 * concepts they teach. Forwards search's `available`/`status` untouched, so an
 * un-ingested KB degrades to `available=false` with empty `hits` (never a 500).
 */
export interface KnowledgeRetrieveResponse {
  query: string;
  k: number;
  available: boolean;
  status: string;
  detail: string | null;
  embedder: string | null;
  hits: KnowledgeSearchHit[];
  concepts: KnowledgeLessonConceptRef[];
}

// ---------------------------------------------------------------------------
// M9a — Coach agents: shared infra + Trade Critic
// ---------------------------------------------------------------------------

/**
 * GET /api/agents/status — whether the agent layer is usable. `available` is
 * false when `TRADEDESK_LLM=none` (or the backend has no configured LLM); the
 * UI hides/disables every agent affordance cleanly in that case (PRD-M9 R1).
 */
export interface AgentStatus {
  available: boolean;
  /** Active LLM backend label (e.g. "anthropic_api", "claude_cli", "none"). */
  llm: string;
}

/** Adherence verdict for a single rule check (PRD-M9 R8). */
export type CriticAdherence = "pass" | "fail" | "unclear";

/** Markdown verdicts for each of the four trade phases (PRD-M9 R8). */
export interface CriticPhaseVerdicts {
  setup: string;
  entry: string;
  management: string;
  exit: string;
}

/**
 * One rule check against the course curriculum. `source_citation === null` means
 * the claim is UNVERIFIED (no supporting passage) and the UI renders it
 * visually distinct (PRD-M9 R3). `evidence_bar_ref` is an ISO-8601 UTC ts that
 * pins the check to a chart bar for an annotation marker (PRD-M9 R9).
 */
export interface CriticRuleCheck {
  rule: string;
  adherence: CriticAdherence;
  source_citation: KnowledgeSearchHit | null;
  evidence_bar_ref: string | null;
  note: string | null;
}

/**
 * Prescribed follow-up. Exactly one of `drill_id` / `drill_spec` is set: an
 * existing M6 drill by id, or a scenario spec the authoring path can create
 * (PRD-M9 R10). `readings` are course lesson ids that deep-link to the reader.
 */
export interface CriticPrescription {
  drill_id: number | null;
  drill_spec: ScenarioCreateSpec | null;
  readings: number[];
}

/**
 * The critic's structured output (PRD-M9 R8). `verdict_by_phase` values are
 * markdown; `rule_checks` carry citations + bar refs; `prescription` links a
 * drill + readings.
 */
export interface CriticResult {
  verdict_by_phase: CriticPhaseVerdicts;
  rule_checks: CriticRuleCheck[];
  prescription: CriticPrescription;
}

/**
 * POST /api/agents/critic response. When `available` is false the agent layer
 * is disabled (no LLM) and `result` is null. `status` distinguishes ok vs
 * error/degraded; `detail` carries user-facing guidance.
 */
export interface CriticResponse {
  available: boolean;
  status: string;
  detail: string | null;
  session_id: number | null;
  llm_backend: string | null;
  result: CriticResult | null;
}

/**
 * A `ScenarioCreate` spec as returned inside a prescription. Mirrors the
 * backend `ScenarioCreate` (see `ScenarioCreateInput` in lib/training.ts); kept
 * as an open record here so the CritiquePanel can POST it verbatim to
 * /api/training/scenarios without a lossy re-map.
 */
export type ScenarioCreateSpec = Record<string, unknown>;

/** One persisted message within an agent session. */
export interface AgentMessage {
  id: number;
  session_id: number;
  role: string;
  content_md: string;
  citations: KnowledgeSearchHit[];
  /** Present on the critic's terminal message; null otherwise. */
  payload: CriticResult | null;
  ts: string;
}

/** GET /api/agents/sessions/{id} — a full conversation, joinable to a trade. */
export interface AgentSessionResponse {
  id: number;
  kind: string;
  subject_ref: string;
  started_at: string;
  messages: AgentMessage[];
}

/** One row in the session list (GET /api/agents/sessions). */
export interface AgentSessionSummary {
  id: number;
  kind: string;
  subject_ref: string;
  started_at: string;
  message_count: number;
}

export interface AgentSessionsResponse {
  sessions: AgentSessionSummary[];
}

export interface AgentSessionsParams {
  subject_ref?: string;
  kind?: string;
}

function toQuery(
  params: Record<string, string | number | boolean | undefined>,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    qs.set(k, String(v));
  }
  return qs.toString();
}

/** GET /api/rules: only the fields the Replay rule bar reads. */
export interface DeskRules {
  window: { start: string; end: string };
  daily_stop: number;
  daily_target: number;
}

/** One position of GET /api/days/{date}: only the fields the Replay rule bar reads. */
export interface DeskDayPosition {
  trade_id: number;
  status: string;
  pnl: number | null;
  fills: { side: string; ts: string }[];
  plan: { caller: string } | null;
}

/** GET /api/days/{date}. The whole day comes back; the UI must filter by time. */
export interface DeskDay {
  date: string;
  positions: DeskDayPosition[];
  fields: { callers: string[] } | null;
}

export const api = {
  health(): Promise<Health> {
    return request<Health>("/health");
  },

  importFills(): Promise<ImportResult> {
    return request<ImportResult>("/import/fills", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({}),
    });
  },

  trades(params: TradesParams = {}): Promise<Trade[]> {
    const qs = toQuery({ ...params });
    return request<Trade[]>(`/trades${qs ? `?${qs}` : ""}`);
  },

  trade(id: number | string): Promise<Trade> {
    return request<Trade>(`/trades/${id}`);
  },

  patchTrade(id: number | string, body: PatchTradeRequest): Promise<Trade> {
    return request<Trade>(`/trades/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  },

  tags(): Promise<Tag[]> {
    return request<Tag[]>("/tags");
  },

  createTag(body: CreateTagRequest): Promise<Tag> {
    return request<Tag>("/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  },

  analyticsSummary(params: TradesParams = {}): Promise<AnalyticsSummary> {
    const qs = toQuery({ ...params });
    return request<AnalyticsSummary>(
      `/analytics/summary${qs ? `?${qs}` : ""}`,
    );
  },

  analyticsCalendar(month: string): Promise<CalendarData> {
    return request<CalendarData>(`/analytics/calendar?${toQuery({ month })}`);
  },

  analyticsBy(dimension: ByDimension): Promise<ByRow[]> {
    return request<ByRow[]>(`/analytics/by?${toQuery({ dimension })}`);
  },

  dailyReview(date: string): Promise<DailyReview> {
    return request<DailyReview>(`/daily-review?${toQuery({ date })}`);
  },

  tradeReview(id: number | string): Promise<TradeReviewResponse> {
    return request<TradeReviewResponse>(`/trades/${id}/review`);
  },

  tradeExcursions(id: number | string): Promise<TradeExcursions> {
    return request<TradeExcursions>(`/trades/${id}/excursions`);
  },

  deskRules(): Promise<DeskRules> {
    return request<DeskRules>("/rules");
  },

  deskDay(date: string): Promise<DeskDay> {
    return request<DeskDay>(`/days/${date}`);
  },

  dayTrades(date: string): Promise<DayTradesResponse> {
    return request<DayTradesResponse>(`/journal/days/${date}/trades`);
  },

  candles({ symbol, date, timeframe = "1m" }: CandlesParams): Promise<Candle[]> {
    const qs = new URLSearchParams({ symbol, date, timeframe });
    return request<Candle[]>(`/candles?${qs.toString()}`);
  },

  replaySession({
    symbol,
    date,
    timeframe = "1m",
    emaPeriods,
    rsiPeriod,
    orMinutes,
  }: ReplaySessionParams): Promise<ReplaySession> {
    const qs = new URLSearchParams({ symbol, date, timeframe });
    if (emaPeriods && emaPeriods.length > 0) {
      qs.set("ema_periods", emaPeriods.join(","));
    }
    if (rsiPeriod != null) qs.set("rsi_period", String(rsiPeriod));
    if (orMinutes != null) qs.set("or_minutes", String(orMinutes));
    return request<ReplaySession>(`/replay/session?${qs.toString()}`);
  },

  replayTrade(tradeId: number, leadMinutes = 15): Promise<ReplayTrade> {
    const qs = new URLSearchParams({ lead_minutes: String(leadMinutes) });
    return request<ReplayTrade>(`/replay/trade/${tradeId}?${qs.toString()}`);
  },

  replayTradeContract(
    tradeId: number,
    leadMinutes = 15,
    playhead?: string,
  ): Promise<ReplayContract> {
    const qs = new URLSearchParams({ lead_minutes: String(leadMinutes) });
    if (playhead) qs.set("playhead", playhead);
    return request<ReplayContract>(`/replay/trade/${tradeId}/contract?${qs.toString()}`);
  },

  replayTradeFills(tradeId: number, playhead: string): Promise<ReplayFills> {
    const qs = new URLSearchParams({ playhead });
    return request<ReplayFills>(`/replay/trade/${tradeId}/fills?${qs.toString()}`);
  },

  createSimTrade(body: CreateSimTradeRequest): Promise<SimTrade> {
    return request<SimTrade>("/sim/trades", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    });
  },

  // M8a — knowledge base
  knowledgeStatus(): Promise<KnowledgeStatus> {
    return request<KnowledgeStatus>("/knowledge/status");
  },

  knowledgeSearch(q: string, k = 8): Promise<KnowledgeSearchResponse> {
    const qs = toQuery({ q, k });
    return request<KnowledgeSearchResponse>(`/knowledge/search?${qs}`);
  },

  // M8b — knowledge base: modules tree + lesson reader + progress
  knowledgeModules(): Promise<KnowledgeModulesResponse> {
    return request<KnowledgeModulesResponse>("/knowledge/modules");
  },

  knowledgeLesson(id: number | string): Promise<KnowledgeLessonResponse> {
    return request<KnowledgeLessonResponse>(`/knowledge/lessons/${id}`);
  },

  knowledgeSetLessonProgress(
    id: number | string,
    status: KnowledgeLessonStatus,
  ): Promise<KnowledgeProgressResponse> {
    return request<KnowledgeProgressResponse>(`/knowledge/lessons/${id}/progress`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ status }),
    });
  },

  // M8c — knowledge base: concepts index + concept detail
  knowledgeConcepts(): Promise<KnowledgeConceptsResponse> {
    return request<KnowledgeConceptsResponse>("/knowledge/concepts");
  },

  knowledgeConcept(slug: string): Promise<KnowledgeConceptDetail> {
    return request<KnowledgeConceptDetail>(`/knowledge/concepts/${slug}`);
  },

  /** Agent-facing retrieval composer (top-k passages + expanded concepts). */
  knowledgeRetrieve(query: string, k = 8): Promise<KnowledgeRetrieveResponse> {
    return request<KnowledgeRetrieveResponse>("/knowledge/retrieve", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ query, k }),
    });
  },

  // M9a — Coach agents: status + Trade Critic + session history
  getAgentStatus(): Promise<AgentStatus> {
    return request<AgentStatus>("/agents/status");
  },

  runCritic(tradeId: number): Promise<CriticResponse> {
    return request<CriticResponse>("/agents/critic", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ trade_id: tradeId }),
    });
  },

  getAgentSession(id: number | string): Promise<AgentSessionResponse> {
    return request<AgentSessionResponse>(`/agents/sessions/${id}`);
  },
  listAgentSessions(
    params: AgentSessionsParams = {},
  ): Promise<AgentSessionsResponse> {
    const qs = toQuery({ ...params });
    return request<AgentSessionsResponse>(
      `/agents/sessions${qs ? `?${qs}` : ""}`,
    );
  },
};
