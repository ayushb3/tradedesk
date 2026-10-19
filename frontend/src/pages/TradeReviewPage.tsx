import { useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  ServerCrash,
  Sparkles,
} from "lucide-react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerBody, DrawerHeader } from "@/components/ui/drawer";
import { Skeleton } from "@/components/ui/skeleton";
import { FillTable } from "@/components/review/FillTable";
import { ReviewRail } from "@/components/review/ReviewRail";
import { ReviewStackChart } from "@/components/review/ReviewStackChart";
import { ExcursionReadout } from "@/components/review/ExcursionReadout";
import { StoryStrip } from "@/components/review/StoryStrip";
import { VerdictBand } from "@/components/review/VerdictBand";
import { CritiquePanel } from "@/components/agents/CritiquePanel";
import { DebriefPanel } from "@/components/review/DebriefPanel";
import { TradeEnrichmentPanel } from "@/components/journal/TradeEnrichmentPanel";
import {
  ApiError,
  type CriticResult,
  type Excursion,
  type ReviewFill,
  type ReviewOption,
  type Trade,
} from "@/lib/api";
import { formatDayMonth } from "@/lib/format";
import {
  useAgentStatus,
  useDayTrades,
  useReviewDay,
  useRunCritic,
  useTradeExcursions,
  useTradeReview,
} from "@/lib/queries";
import { dayTally } from "@/lib/tradeReview";

/**
 * Per-trade review: verdict band, story strip, the static stacked contract-over-SPY chart,
 * the fills table and a slim rail (Replay, what was written before entry, setup tag, lesson,
 * call source). Grade, checklist, screenshots and the Debrief live behind "More". Process-first
 * framing: this is a review surface, never a celebration.
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
          option={data.option}
          excursion={data.excursion}
          initialTab={initialTab}
        />
      ) : null}
    </div>
  );
}

/** Tabs inside the "More" drawer. */
type RightTab = "journal" | "debrief";

function ReviewContent({
  trade,
  fills,
  option,
  excursion,
  initialTab,
}: {
  trade: Trade;
  fills: ReviewFill[];
  option: ReviewOption | null;
  excursion: Excursion | null;
  initialTab: RightTab;
}) {
  const navigate = useNavigate();
  const [rightTab, setRightTab] = useState<RightTab>(initialTab);
  // ?debrief=1 (old debrief links) opens the drawer straight on the Debrief tab.
  const [moreOpen, setMoreOpen] = useState(initialTab === "debrief");
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

  const dayTrades = useDayTrades(trade.session_date);
  const ids = (dayTrades.data?.trades ?? []).map((t) => t.id);
  const idx = ids.indexOf(trade.id);
  const prevId = idx > 0 ? ids[idx - 1] : null;
  const nextId = idx >= 0 && idx < ids.length - 1 ? ids[idx + 1] : null;

  const label = trade.contract_label ?? trade.symbol;

  // Option trades only: MAE/MFE, the stop line and the post-exit path (#51).
  const excursions = useTradeExcursions(trade.id, option != null);

  // Verdict band: the day tally through this trade's close.
  const reviewDay = useReviewDay(trade.session_date);
  const tally = dayTally(reviewDay.data, trade.id);

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
          <VerdictBand
            trade={trade}
            label={label}
            tally={tally}
          />
          {option && (
            <StoryStrip
              data={excursions.data}
              isLoading={excursions.isLoading}
              isError={excursions.isError}
            />
          )}

          <div className="grid gap-4 lg:grid-cols-[1fr_400px]">
            {/* Chart + fills column */}
            <div className="flex flex-col gap-4">
              <ReviewStackChart trade={trade} fills={fills} hasOption={option != null} />

              {/* Share trades carry their MFE/MAE here (basis: underlying); option trades
                  show it in the story strip above. */}
              {!option && excursion && <ExcursionReadout excursion={excursion} />}

              <section className="space-y-2">
                <h2 className="text-body font-semibold tracking-tight">Fills</h2>
                <FillTable fills={fills} hoveredFillId={null} onHoverFill={() => {}} />
              </section>

              {/* Trade Critic output (PRD-M9). Errors surface inline; a
                  successful run renders the cited rule checks + prescription,
                  per-rule checks and the prescription. */}
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

            <ReviewRail trade={trade} excursions={excursions.data} onOpenMore={() => setMoreOpen(true)} />
          </div>
        </div>
      </div>

      <Drawer open={moreOpen} onClose={() => setMoreOpen(false)} title="More about this trade" className="max-w-lg">
        <DrawerHeader>
          <div className="flex items-center gap-2">
            {(["journal", "debrief"] as const).map((t) => (
              <Button
                key={t}
                variant={rightTab === t ? "default" : "outline"}
                size="sm"
                onClick={() => setRightTab(t)}
              >
                {t === "journal" ? "Grade and checklist" : "Debrief"}
              </Button>
            ))}
          </div>
        </DrawerHeader>
        <DrawerBody className="flex flex-col gap-4">
          {rightTab === "journal" ? (
            <TradeEnrichmentPanel trade={trade} omit={{ tags: true, notes: true }} />
          ) : (
            <div className="flex min-h-[480px] flex-1 flex-col">
              <DebriefPanel
                key={trade.id}
                tradeId={trade.id}
                onGetCritique={
                  agentAvailable
                    ? () => {
                        setMoreOpen(false);
                        focusCritique();
                      }
                    : undefined
                }
              />
            </div>
          )}
        </DrawerBody>
      </Drawer>
    </>
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
