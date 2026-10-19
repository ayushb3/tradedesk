import { useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  MessageCircle,
  ServerCrash,
  Sparkles,
} from "lucide-react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { FillTable } from "@/components/review/FillTable";
import { ReviewChart, type RuleCheckMarker } from "@/components/review/ReviewChart";
import { OptionPremiumSection } from "@/components/review/OptionPremiumSection";
import { ExcursionReadout } from "@/components/review/ExcursionReadout";
import { TradeExcursionsPanel } from "@/components/review/TradeExcursionsPanel";
import { CritiquePanel } from "@/components/agents/CritiquePanel";
import { DebriefPanel } from "@/components/review/DebriefPanel";
import { TradeEnrichmentPanel } from "@/components/journal/TradeEnrichmentPanel";
import {
  ApiError,
  type Candle,
  type CriticResult,
  type Excursion,
  type ReviewFill,
  type ReviewOption,
  type Timeframe,
  type Trade,
} from "@/lib/api";
import {
  formatClock,
  formatCurrency,
  formatDayMonth,
  formatHoldTime,
  formatNumber,
  formatPercent,
  formatPnl,
  pnlColorClass,
  DISPLAY_TZ_LABEL,
} from "@/lib/format";
import {
  useAgentStatus,
  useCandles,
  useDayTrades,
  useRunCritic,
  useTradeExcursions,
  useTradeReview,
} from "@/lib/queries";
import { cn } from "@/lib/utils";

/** Timeframes the review chart offers (subset of the underlying descriptor). */
const REVIEW_TIMEFRAMES: readonly ("1m" | "5m")[] = ["1m", "5m"] as const;

/**
 * Per-trade review: header stats + a static underlying chart with entry/exit
 * fill markers, a hover-linked fill table, the shared enrichment editor, and
 * prev/next navigation within the same session day. Process-first framing —
 * this is a review surface, never a celebration.
 */
export function TradeReviewPage() {
  const { id = "" } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  // ?debrief=1 opens the cockpit's right column on the Debrief tab. Old
  // /debriefs/new/:tradeId links redirect here with this param set (#63/#62).
  const initialTab: RightTab = searchParams.get("debrief") === "1" ? "debrief" : "journal";
  const { data, isLoading, isError, error } = useTradeReview(id);

  return (
    <div className="flex h-full flex-col">
      {isLoading ? (
        <LoadingBody />
      ) : isError ? (
        <ErrorBody error={error} />
      ) : data ? (
        <ReviewContent
          trade={data.trade}
          fills={data.fills}
          timeframes={data.underlying.timeframes}
          option={data.option}
          premiumBars={data.premium_bars}
          excursion={data.excursion}
          initialTab={initialTab}
        />
      ) : null}
    </div>
  );
}

/** Right-column tabs in the cockpit. */
type RightTab = "journal" | "debrief";

function ReviewContent({
  trade,
  fills,
  timeframes,
  option,
  premiumBars,
  excursion,
  initialTab,
}: {
  trade: Trade;
  fills: ReviewFill[];
  timeframes: Timeframe[];
  option: ReviewOption | null;
  premiumBars: Candle[] | null;
  excursion: Excursion | null;
  initialTab: RightTab;
}) {
  const navigate = useNavigate();
  const [hoveredFillId, setHoveredFillId] = useState<number | null>(null);
  const [rightTab, setRightTab] = useState<RightTab>(initialTab);
  const critiqueRef = useRef<HTMLDivElement>(null);

  // ---- Trade Critic (PRD-M9 R6/R9) ----------------------------------------
  // Gated on agent availability: with TRADEDESK_LLM=none the status endpoint
  // returns available=false and the whole Critique affordance is hidden — no
  // critic request is ever fired.
  const agentStatus = useAgentStatus();
  const critic = useRunCritic();
  const [critique, setCritique] = useState<CriticResult | null>(null);
  const agentAvailable = agentStatus.data?.available === true;

  const runCritique = () => {
    critic.mutate(trade.id, {
      onSuccess: (res) => {
        // available may flip false mid-session (LLM removed) — respect it.
        if (res.available && res.result) setCritique(res.result);
      },
    });
  };

  // "Get critique" from the Debrief tab: the critique renders in THIS page's
  // left column, so we trigger it in place (if not already run) and scroll it
  // into view — no route change (#63). Reveals the left column even though the
  // user is on the Debrief tab in the right column.
  const focusCritique = () => {
    if (!critique && !critic.isPending) runCritique();
    // Defer the scroll so the critique section has a chance to mount.
    requestAnimationFrame(() =>
      critiqueRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
    );
  };

  // Rule-check → chart marker mapping (R9): only checks that pin to a bar.
  const ruleCheckMarkers: RuleCheckMarker[] = (critique?.rule_checks ?? [])
    .filter((rc) => rc.evidence_bar_ref != null)
    .map((rc) => ({
      iso: rc.evidence_bar_ref as string,
      adherence: rc.adherence,
      label: rc.rule.length > 24 ? `${rc.rule.slice(0, 22)}…` : rc.rule,
    }));

  // Timeframe options: 1m/5m only for review (contract §4). Default 1m.
  const options = REVIEW_TIMEFRAMES.filter((tf) => timeframes.includes(tf));
  const available = options.length > 0 ? options : (["1m"] as const);
  const [timeframe, setTimeframe] = useState<"1m" | "5m">(available[0]);

  const candles = useCandles({
    symbol: trade.symbol,
    date: trade.session_date,
    timeframe,
  });

  // Prev/next within the same session day — neighbors derived from the day's
  // chronological trade list.
  const dayTrades = useDayTrades(trade.session_date);
  const ids = (dayTrades.data?.trades ?? []).map((t) => t.id);
  const idx = ids.indexOf(trade.id);
  const prevId = idx > 0 ? ids[idx - 1] : null;
  const nextId = idx >= 0 && idx < ids.length - 1 ? ids[idx + 1] : null;

  const label = trade.contract_label ?? trade.symbol;

  // Option trades only: MAE/MFE, the stop line and the post-exit path (#51).
  const excursions = useTradeExcursions(trade.id, option != null);

  return (
    <>
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate(`/journal/${trade.session_date}`)}
          className="gap-1.5"
        >
          <ArrowLeft className="h-4 w-4" />
          {formatDayMonth(trade.session_date)}
        </Button>

        {/* Critique (PRD-M9 R6) — only when the agent layer is available. */}
        {agentAvailable && (
          <Button
            variant="outline"
            size="sm"
            onClick={runCritique}
            disabled={critic.isPending}
            className="gap-1.5"
          >
            <Sparkles className="h-4 w-4" />
            {critic.isPending
              ? "Critiquing…"
              : critique
                ? "Re-run critique"
                : "Critique"}
          </Button>
        )}

        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            disabled={prevId == null}
            onClick={() => prevId != null && navigate(`/trades/${prevId}`)}
            className="gap-1"
          >
            <ChevronLeft className="h-4 w-4" />
            Prev
          </Button>
          {idx >= 0 && ids.length > 0 && (
            <span className="tabular px-1 text-xs text-muted-foreground">
              {idx + 1} / {ids.length}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={nextId == null}
            onClick={() => nextId != null && navigate(`/trades/${nextId}`)}
            className="gap-1"
          >
            Next
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-6xl flex-col gap-4">
          <ReviewHeader trade={trade} label={label} />

          <div className="grid gap-4 lg:grid-cols-[1fr_400px]">
            {/* Chart + fills column */}
            <div className="flex flex-col gap-4">
              {/* Option premium chart (options only) — the position's own price
                  curve. Placed first: for an option trade the premium is what
                  the trader actually experienced (PRD-M7 R4/R5/R9). */}
              {option && (
                <OptionPremiumSection
                  option={option}
                  premiumBars={premiumBars}
                  fills={fills}
                  excursion={excursion}
                  stopPrice={excursions.data?.stop_price ?? null}
                  trade={trade}
                  hoveredFillId={hoveredFillId}
                  onHoverFill={setHoveredFillId}
                />
              )}
              {option && (
                <TradeExcursionsPanel
                  data={excursions.data}
                  isLoading={excursions.isLoading}
                  isError={excursions.isError}
                />
              )}

              <section className="space-y-2">
                <div className="flex flex-wrap items-center gap-3">
                  <h2 className="text-sm font-semibold tracking-tight">
                    {trade.symbol} underlying
                  </h2>
                  {/* Share trades carry their MFE/MAE here (basis: underlying);
                      option trades show it in the premium section instead. */}
                  {!option && excursion && (
                    <ExcursionReadout excursion={excursion} />
                  )}
                  <div className="ml-auto flex items-center gap-2">
                    <Label htmlFor="tf" className="text-xs">
                      Timeframe
                    </Label>
                    <Select
                      value={timeframe}
                      onValueChange={(v) => setTimeframe(v as "1m" | "5m")}
                    >
                      <SelectTrigger id="tf" className="tabular h-8 w-20">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {available.map((tf) => (
                          <SelectItem key={tf} value={tf} className="tabular">
                            {tf}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="h-[420px] rounded-lg border border-border bg-background/40 p-1">
                  {candles.isLoading ? (
                    <Skeleton className="h-full w-full" />
                  ) : candles.isError ? (
                    <div className="flex h-full items-center justify-center p-4">
                      <Alert variant="default" className="max-w-md">
                        <AlertCircle className="h-4 w-4" />
                        <AlertTitle>Couldn't load the chart</AlertTitle>
                        <AlertDescription>
                          No candles returned for{" "}
                          <span className="tabular">{trade.symbol}</span> on{" "}
                          <span className="tabular">{trade.session_date}</span>.
                        </AlertDescription>
                      </Alert>
                    </div>
                  ) : (
                    <ReviewChart
                      candles={candles.data ?? []}
                      fills={fills}
                      timeframe={timeframe}
                      hoveredFillId={hoveredFillId}
                      onHoverFill={setHoveredFillId}
                      ruleCheckMarkers={ruleCheckMarkers}
                    />
                  )}
                </div>
              </section>

              <section className="space-y-2">
                <h2 className="text-sm font-semibold tracking-tight">Fills</h2>
                <FillTable
                  fills={fills}
                  hoveredFillId={hoveredFillId}
                  onHoverFill={setHoveredFillId}
                />
              </section>

              {/* Trade Critic output (PRD-M9). Errors surface inline; a
                  successful run renders the cited rule checks + prescription,
                  and its bar refs annotate the chart above. */}
              {agentAvailable && (critic.isError || critique) && (
                <section ref={critiqueRef} className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Sparkles className="h-4 w-4 text-primary" />
                    <h2 className="text-sm font-semibold tracking-tight">
                      Critique
                    </h2>
                    <span className="text-xs text-muted-foreground">
                      per course material — process, not P&amp;L
                    </span>
                  </div>
                  {critic.isError ? (
                    <Alert variant="default">
                      <AlertCircle className="h-4 w-4" />
                      <AlertTitle>Couldn't run the critique</AlertTitle>
                      <AlertDescription>
                        {critic.error instanceof ApiError
                          ? (critic.error.detail ?? critic.error.message)
                          : "Something went wrong. Try again."}
                      </AlertDescription>
                    </Alert>
                  ) : critique ? (
                    <CritiquePanel result={critique} />
                  ) : null}
                </section>
              )}
            </div>

            {/* Right column: Journal | Debrief tabs. The debrief interview
                lives here (epic #64 / #63) so the annotated chart stays
                visible in the left column throughout. */}
            <aside className="flex min-h-0 flex-col gap-4 rounded-lg border border-border bg-card/40 p-4">
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  variant={rightTab === "journal" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setRightTab("journal")}
                >
                  Journal
                </Button>
                <Button
                  variant={rightTab === "debrief" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setRightTab("debrief")}
                  className="gap-1.5"
                >
                  <MessageCircle className="h-3.5 w-3.5" />
                  Debrief
                </Button>
              </div>

              {rightTab === "journal" ? (
                <TradeEnrichmentPanel trade={trade} />
              ) : (
                <div className="flex min-h-[480px] flex-col">
                  <DebriefPanel
                    key={trade.id}
                    tradeId={trade.id}
                    onGetCritique={agentAvailable ? focusCritique : undefined}
                  />
                </div>
              )}
            </aside>
          </div>
        </div>
      </div>
    </>
  );
}

function ReviewHeader({ trade, label }: { trade: Trade; label: string }) {
  return (
    <div className="rounded-lg border border-border bg-background/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-lg font-semibold tracking-tight">{label}</span>
        <span
          className={cn(
            "text-xs font-medium uppercase",
            trade.direction === "long" ? "text-[#26a69a]" : "text-[#ef5350]",
          )}
        >
          {trade.direction}
        </span>
        {trade.simulated && <Badge variant="muted">sim</Badge>}
        <Badge variant="outline" className="capitalize">
          {trade.status}
        </Badge>
        {trade.enrichment?.grade && (
          <Badge variant="outline">Grade {trade.enrichment.grade}</Badge>
        )}
        <span className="tabular ml-auto text-xs text-muted-foreground">
          {formatClock(trade.open_ts)}
          {trade.close_ts ? ` → ${formatClock(trade.close_ts)}` : ""} {DISPLAY_TZ_LABEL} ·{" "}
          {formatHoldTime(trade.hold_time_s)}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Qty">
          <span className="tabular">{formatNumber(trade.qty, 0)}</span>
        </Stat>
        <Stat label="Entry → Exit">
          <span className="tabular">
            {formatNumber(trade.avg_entry)} → {formatNumber(trade.avg_exit)}
          </span>
        </Stat>
        <Stat label="Net P&L">
          <span className={cn("tabular font-medium", pnlColorClass(trade.net_pnl))}>
            {formatPnl(trade.net_pnl)}
          </span>
        </Stat>
        <Stat label="P&L %">
          <span className={cn("tabular", pnlColorClass(trade.pnl_pct))}>
            {formatPercent(trade.pnl_pct, { fromRatio: false, digits: 2 })}
          </span>
        </Stat>
        <Stat label="R multiple">
          <span className={cn("tabular", pnlColorClass(trade.r_multiple))}>
            {formatNumber(trade.r_multiple)}
          </span>
        </Stat>
        <Stat label="Fees">
          <span className="tabular text-muted-foreground">
            {formatCurrency(trade.fees)}
          </span>
        </Stat>
      </div>
    </div>
  );
}

function Stat({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="text-sm">{children}</span>
    </div>
  );
}

function LoadingBody() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Skeleton className="h-8 w-24" />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-6xl flex-col gap-4">
          <Skeleton className="h-24 w-full rounded-lg" />
          <div className="grid gap-4 lg:grid-cols-[1fr_400px]">
            <Skeleton className="h-[420px] w-full rounded-lg" />
            <Skeleton className="h-[420px] w-full rounded-lg" />
          </div>
        </div>
      </div>
    </div>
  );
}

function ErrorBody({ error }: { error: unknown }) {
  const navigate = useNavigate();
  const status = error instanceof ApiError ? error.status : undefined;
  const backendDown = status === 0;
  const notFound = status === 404;
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate("/journal")}
          className="gap-1.5"
        >
          <ArrowLeft className="h-4 w-4" />
          Journal
        </Button>
      </div>
      <div className="flex flex-1 items-center justify-center p-6">
        <Alert
          variant={notFound ? "default" : "destructive"}
          className="max-w-md"
        >
          {backendDown ? (
            <ServerCrash className="h-4 w-4" />
          ) : (
            <AlertCircle className="h-4 w-4" />
          )}
          <AlertTitle>
            {backendDown
              ? "Backend not running"
              : notFound
                ? "Trade not found"
                : "Couldn't load this trade"}
          </AlertTitle>
          <AlertDescription>
            {backendDown ? (
              <>
                Start it with{" "}
                <code className="tabular rounded bg-muted px-1 py-0.5">
                  make backend
                </code>
                .
              </>
            ) : notFound ? (
              "This trade doesn't exist. It may have been rebuilt on a fresh import."
            ) : error instanceof ApiError ? (
              error.detail || error.message
            ) : (
              "Something went wrong loading this trade."
            )}
          </AlertDescription>
        </Alert>
      </div>
    </div>
  );
}
