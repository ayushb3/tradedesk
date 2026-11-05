import type { Candle, ReplayFill, ReplayRealResult } from "@/lib/api";
import { formatPnl, formatR } from "@/lib/format";
import { stopVerdict, summarizeRealTrade } from "@/lib/tradeResult";
import { FillsList } from "@/components/replay/FillsList";

const tone = (n: number) => (n > 0 ? "text-gain" : n < 0 ? "text-loss" : "text-foreground");
const px = (n: number) => `$${n.toFixed(2)}`;

const lastTimePt = (fills: ReplayFill[]) =>
  [...fills].sort((a, b) => a.seq - b.seq)[fills.length - 1]?.time_pt ?? "";

/**
 * "Your real trade". Nothing renders until the server has revealed the entry
 * fill (it only returns fills at or before the playhead). The exit, real R and
 * stop verdict are shown ONLY when `exitReached` (the server's window_end_ts,
 * which exists only once the playhead is at or past the exit) is true. Net $ and
 * R come from the server (`realResult`, same gate); the client does not compute
 * them from gross prices. The stop verdict stays client-side.
 *
 * Compact on purpose: the chart is the focus of a replay. Rows at label size, R and net on
 * one line, then the per-fill list (time, qty @ price, value, order type).
 */
export function RealTradeCard({
  fills,
  writtenStop,
  stopAssumed,
  exitReached,
  realResult,
  bars,
  sourceLabel,
  onSeekFill,
}: {
  /** Fills the server revealed (already gated by the playhead). */
  fills: ReplayFill[];
  writtenStop: number | null;
  /** The API says no max loss was written, so the stop is the default one. */
  stopAssumed: boolean;
  exitReached: boolean;
  /** The server's NET $ and R; it sends them only once the playhead is at or past the exit. */
  realResult: ReplayRealResult | null;
  /** Premium bars; only used once the exit is reached. */
  bars: Candle[] | null;
  sourceLabel?: string;
  /** Clicking a fill row moves the playhead to it. */
  onSeekFill?: (fill: ReplayFill) => void;
}) {
  const entries = fills.filter((f) => f.role === "entry");
  const summary = summarizeRealTrade(fills, writtenStop, exitReached, stopAssumed, realResult);

  if (!summary) {
    return (
      <section className="border-b border-border px-3 py-2" aria-label="Your real trade">
        <div className="label-caps text-label font-semibold text-muted-foreground">
          Your real trade
        </div>
        <p className="mt-0.5 text-label text-muted-foreground">
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

  const r = summary.exit?.r ?? null;
  const pnl = summary.exit?.pnl ?? null;
  return (
    <section className="border-b border-border" aria-label="Your real trade">
      <div className="space-y-1 px-3 pt-2">
        <div className="label-caps text-label font-semibold text-muted-foreground">Your real trade</div>

        <dl className="num grid grid-cols-[auto_1fr] gap-x-3 text-label">
          <dt className="text-muted-foreground">Entry</dt>
          <dd className="whitespace-nowrap text-right">
            {lastTimePt(entries)} &middot; {px(summary.avgIn)}
            {summary.qtyIn > 1 ? ` ×${summary.qtyIn}` : ""}
          </dd>
          <dt className="text-muted-foreground">Stop{summary.stopAssumed ? " (assumed)" : ""}</dt>
          <dd className="text-right text-stop">{px(summary.stop)}</dd>
          {summary.exit && (
            <>
              <dt className="text-muted-foreground">Exit</dt>
              <dd className="whitespace-nowrap text-right">
                {lastTimePt(exitFills)} &middot; {px(summary.exit.avgOut)}
              </dd>
            </>
          )}
        </dl>

        {summary.exit ? (
          <>
            <div className="num flex items-baseline gap-2 whitespace-nowrap text-value">
              <span className={r === null ? "text-muted-foreground" : tone(r)}>
                {r === null ? "R n/a" : formatR(r)}
                {summary.exit.rBasis === "assumed" && (
                  <span className="text-label text-muted-foreground" title="R uses an assumed -30% stop: no max loss was written for this trade.">
                    {" "}assumed
                  </span>
                )}
              </span>
              <span className={pnl === null ? "text-muted-foreground" : tone(pnl)}>
                {pnl === null ? "net n/a" : `${formatPnl(pnl)} net`}
              </span>
            </div>
            {verdict && (
              <p className={`text-label ${verdict.respected ? "text-gain" : "text-loss"}`}>
                {verdict.respected ? (
                  <>&#10003; Stop respected</>
                ) : (
                  <>
                    &#10007; Held <span className="num">{verdict.minutesPast}</span> min past stop
                  </>
                )}
              </p>
            )}
          </>
        ) : (
          <p className="text-label text-muted-foreground">Exit, R and stop verdict appear at your exit.</p>
        )}
      </div>
      <FillsList fills={fills} onSeek={onSeekFill} />
    </section>
  );
}
