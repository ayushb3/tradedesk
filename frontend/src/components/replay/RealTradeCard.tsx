import type { Candle, ReplayFill } from "@/lib/api";
import { formatPnl, formatR } from "@/lib/format";
import { stopVerdict, summarizeRealTrade } from "@/lib/tradeResult";
import { StopVerdictLine } from "@/components/replay/ResultCard";

const tone = (n: number) => (n > 0 ? "text-gain" : n < 0 ? "text-loss" : "text-foreground");
const px = (n: number) => `$${n.toFixed(2)}`;

const lastTimePt = (fills: ReplayFill[]) =>
  [...fills].sort((a, b) => a.seq - b.seq)[fills.length - 1]?.time_pt ?? "";

/**
 * "Your real trade". Nothing renders until the server has revealed the entry
 * fill (it only returns fills at or before the playhead). The exit, real R and
 * stop verdict are computed ONLY when `exitReached` (the server's window_end_ts,
 * which exists only once the playhead is at or past the exit) is true, so there
 * is nothing about the exit in the DOM or in memory before then.
 */
export function RealTradeCard({
  fills,
  writtenStop,
  stopAssumed,
  exitReached,
  bars,
  sourceLabel,
}: {
  /** Fills the server revealed (already gated by the playhead). */
  fills: ReplayFill[];
  writtenStop: number | null;
  /** The API says no max loss was written, so the stop is the default one. */
  stopAssumed: boolean;
  exitReached: boolean;
  /** Premium bars; only used once the exit is reached. */
  bars: Candle[] | null;
  sourceLabel?: string;
}) {
  const entries = fills.filter((f) => f.role === "entry");
  const summary = summarizeRealTrade(fills, writtenStop, exitReached, stopAssumed);

  if (!summary) {
    return (
      <section className="border-b border-border p-3" aria-label="Your real trade">
        <div className="label-caps text-label font-semibold text-muted-foreground">
          Your real trade
        </div>
        <p className="mt-1 text-label text-muted-foreground">
          Hidden until the playhead reaches your entry. Decide first, then compare.
          {sourceLabel ? ` Bars: ${sourceLabel}.` : ""}
        </p>
      </section>
    );
  }

  const exitFills = fills.filter((f) => f.role === "exit");
  const verdict =
    summary.exit && bars
      ? stopVerdict({
          bars,
          openTs: summary.openTs,
          flatTs: summary.exit.flatTs,
          stops: [{ ts: summary.openTs, stop: summary.stop }],
        })
      : null;

  return (
    <section className="space-y-2 border-b border-border p-3" aria-label="Your real trade">
      <div className="label-caps text-label font-semibold text-muted-foreground">
        Your real trade
      </div>

      <dl className="num grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-value">
        <dt className="text-label text-muted-foreground">Entry (PT)</dt>
        <dd className="text-right">
          {lastTimePt(entries)} &middot; {px(summary.avgIn)}
          {summary.qtyIn > 1 ? ` ×${summary.qtyIn}` : ""}
        </dd>
        <dt className="text-label text-muted-foreground">
          Stop{summary.stopAssumed ? " (assumed)" : " (written)"}
        </dt>
        <dd className="text-right text-stop">{px(summary.stop)}</dd>
        {summary.exit && (
          <>
            <dt className="text-label text-muted-foreground">Exit (PT)</dt>
            <dd className="text-right">
              {lastTimePt(exitFills)} &middot; {px(summary.exit.avgOut)}
            </dd>
          </>
        )}
      </dl>

      {summary.exit ? (
        <div className="space-y-1">
          <div
            className={`num text-key leading-none ${
              summary.exit.r === null ? "text-muted-foreground" : tone(summary.exit.r)
            }`}
          >
            {summary.exit.r === null ? "R n/a" : formatR(summary.exit.r)}
          </div>
          <div className={`num text-value ${tone(summary.exit.pnl)}`}>
            {formatPnl(summary.exit.pnl)}
          </div>
          {verdict && <StopVerdictLine verdict={verdict} />}
          {summary.stopAssumed && (
            <p className="text-label text-muted-foreground">
              R uses an assumed -30% stop: no max loss was written for this trade.
            </p>
          )}
        </div>
      ) : (
        <p className="text-label text-muted-foreground">
          Exit, R and stop verdict appear when the playhead reaches your exit.
        </p>
      )}
    </section>
  );
}
