import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Download, Table2 } from "lucide-react";

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
import { useToast } from "@/components/ui/toast";
import { SYMBOLS } from "@/components/replay/ReplayToolbar";
import { ApiErrorState, EmptyState } from "@/components/journal/ApiStates";
import { StatCards } from "@/components/journal/StatCards";
import { PnlCalendar } from "@/components/journal/PnlCalendar";
import { Breakdown } from "@/components/journal/Breakdowns";
import { GroupedTradesTable } from "@/components/journal/GroupedTradesTable";
import { RuleScoreboard } from "@/components/days/RuleScoreboard";
import { PatternsSection } from "@/components/days/PatternsSection";
import { calendarMarks, inMonth, ruleBreaks, ruleScoreboard } from "@/lib/daysHome";
import { ApiError, type TradesParams } from "@/lib/api";
import {
  useAnalyticsBy,
  useAnalyticsCalendar,
  useAnalyticsSummary,
  useDeskDays,
  useHealth,
  useImportFills,
  useTrades,
} from "@/lib/queries";
import { DISPLAY_TZ_LABEL } from "@/lib/format";

const ALL = "all";

/** Current month as `YYYY-MM` (local clock). */
function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** Human-readable description for an import failure, preferring the backend's
 * actionable `detail` (e.g. the Webull token-pending / subscription message). */
function importErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.detail) return err.detail;
    if (err.status === 0)
      return "Could not reach the backend. Is it running?";
  }
  return "Could not import fills. Please try again.";
}

export function JournalPage() {
  const { toast } = useToast();
  const navigate = useNavigate();

  const [symbol, setSymbol] = useState<string>(ALL);
  const [sim, setSim] = useState<string>(ALL);
  const [month, setMonth] = useState<string>(currentMonth());

  const params: TradesParams = {
    ...(symbol !== ALL ? { symbol } : {}),
    ...(sim !== ALL ? { simulated: sim === "sim" } : {}),
  };

  const health = useHealth();
  const isLive = health.data?.mode === "webull";

  const importFills = useImportFills();
  const trades = useTrades(params);
  const summary = useAnalyticsSummary(params);
  const calendar = useAnalyticsCalendar(month);
  const deskDays = useDeskDays();
  const byTicker = useAnalyticsBy("ticker");
  const byTod = useAnalyticsBy("tod");

  const runImport = () => {
    importFills.mutate(undefined, {
      onSuccess: (res) =>
        toast({
          title: "Fills imported",
          description: `${res.fills_imported} fills · ${res.trades_built} trades built`,
          variant: "success",
        }),
      onError: (err) =>
        toast({
          title: "Import failed",
          description: importErrorMessage(err),
          variant: "destructive",
        }),
    });
  };

  // Auto-import the user's real Webull trades when the Journal opens in live
  // (webull) mode — so real fills appear without a manual click. Runs once per
  // mount, only after health confirms live mode. In mock mode we never auto-
  // import, so demo data can't silently masquerade as real trades; the manual
  // button below still works in any mode.
  const autoImportedRef = useRef(false);
  useEffect(() => {
    if (!isLive || autoImportedRef.current) return;
    autoImportedRef.current = true;
    runImport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLive]);

  const monthDays = (deskDays.data?.days ?? []).filter((d) => inMonth(d.date, month));
  const board = ruleScoreboard(monthDays);
  const marks = calendarMarks(monthDays);
  const monthName = new Date(`${month}-01T12:00:00Z`).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });

  const tradeList = trades.data ?? [];
  const monthTrades = tradeList.filter((t) => inMonth(t.session_date, month));
  const breaks = ruleBreaks(monthTrades);
  const hasTrades = tradeList.length > 0;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4">
        <h1 className="font-semibold tracking-tight">Journal</h1>

        <div className="ml-auto flex items-center gap-3">
          <div className="hidden items-center gap-2 sm:flex">
            <Label htmlFor="j-symbol">Symbol</Label>
            <Select value={symbol} onValueChange={setSymbol}>
              <SelectTrigger id="j-symbol" className="h-8 w-28 tabular">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All</SelectItem>
                {SYMBOLS.map((s) => (
                  <SelectItem key={s} value={s} className="tabular">
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="hidden items-center gap-2 sm:flex">
            <Label htmlFor="j-sim">Type</Label>
            <Select value={sim} onValueChange={setSim}>
              <SelectTrigger id="j-sim" className="h-8 w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All</SelectItem>
                <SelectItem value="real">Real</SelectItem>
                <SelectItem value="sim">Simulated</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button onClick={runImport} disabled={importFills.isPending}>
            <Download className="h-4 w-4" />
            {importFills.isPending
              ? isLive
                ? "Syncing…"
                : "Importing…"
              : isLive
                ? "Refresh trades"
                : "Import fills"}
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-6xl flex-col gap-4">
          <RuleScoreboard board={board} label={monthName} />

          <StatCards summary={summary.data} loading={summary.isLoading} />

          <div className="grid gap-4 lg:grid-cols-2">
            <PnlCalendar
              month={month}
              data={calendar.data}
              loading={calendar.isLoading}
              onMonthChange={setMonth}
              onSelectDay={(date) => navigate(`/journal/${date}`)}
              marks={marks}
            />
            <div className="grid gap-4">
              <Breakdown
                title="By ticker"
                keyLabel="Ticker"
                rows={byTicker.data}
                loading={byTicker.isLoading}
                isError={byTicker.isError}
              />
              <Breakdown
                title={`By time of day (${DISPLAY_TZ_LABEL})`}
                keyLabel="Session"
                rows={byTod.data}
                loading={byTod.isLoading}
                isError={byTod.isError}
              />
            </div>
          </div>

          <section className="space-y-2">
            <div className="flex items-center gap-2">
              <Table2 className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-semibold tracking-tight">Trades</h2>
              {hasTrades && (
                <span className="tabular text-xs text-muted-foreground">
                  {tradeList.length}
                </span>
              )}
            </div>

            {trades.isLoading ||
            (importFills.isPending && !hasTrades) ||
            (health.isLoading && !hasTrades) ? (
              <Skeleton className="h-64 w-full rounded-lg" />
            ) : trades.isError ? (
              <ApiErrorState error={trades.error} />
            ) : hasTrades ? (
              <GroupedTradesTable
                trades={tradeList}
                onSelect={(id) => navigate(`/trades/${id}`)}
              />
            ) : (
              <EmptyState
                title="No trades yet"
                description={
                  isLive
                    ? "No filled Webull trades found yet. Once you have executed trades, they’ll sync here automatically with stats, a P&L calendar, and per-trade journaling."
                    : "Import fills to build your round-trip trades, then they’ll show up here with stats, a P&L calendar, and per-trade journaling."
                }
                action={{
                  label: isLive ? "Refresh trades" : "Import fills",
                  onClick: runImport,
                  loading: importFills.isPending,
                }}
              />
            )}
          </section>

          <PatternsSection breaks={breaks} />
        </div>
      </div>
    </div>
  );
}
