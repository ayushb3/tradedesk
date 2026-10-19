import type { Candle } from "@/lib/api";
import { formatPnl, formatR } from "@/lib/format";
import type { RoundTrip } from "@/lib/simPosition";
import { stopVerdict, type StopVerdict } from "@/lib/tradeResult";

const tone = (n: number) => (n > 0 ? "text-gain" : n < 0 ? "text-loss" : "text-foreground");
const px = (n: number) => `$${n.toFixed(2)}`;

/** One line: "✓ Stop respected" (green) or "✗ Held N min past stop" (red). */
export function StopVerdictLine({ verdict }: { verdict: StopVerdict }) {
  return verdict.respected ? (
    <p className="text-body font-medium text-gain">&#10003; Stop respected</p>
  ) : (
    <p className="text-body font-medium text-loss">
      &#10007; Held <span className="num">{verdict.minutesPast}</span> min past stop
    </p>
  );
}

/**
 * Result of one completed sim round trip: R is the hero, then $ net, then the
 * stop verdict and the "added lower" flag (a flag only, it never blocks).
 * `hero` is true for the newest trip only (one hero per screen).
 */
export function ResultCard({
  trip,
  index,
  bars,
  hero,
}: {
  trip: RoundTrip;
  index: number;
  /** Premium bars; the verdict only looks at the minutes the position was open. */
  bars: Candle[];
  hero: boolean;
}) {
  const first = trip.fills[0];
  const last = trip.fills[trip.fills.length - 1];
  const verdict = stopVerdict({
    bars,
    openTs: first.ts,
    flatTs: last.ts,
    stops: trip.stops,
  });

  return (
    <section
      aria-label={`Round trip ${index + 1} result`}
      className="space-y-1 rounded-md border border-border bg-card px-3 py-3"
    >
      <div className="label-caps text-label font-semibold text-muted-foreground">
        Round trip #{index + 1}
      </div>
      <div className={`num ${hero ? "text-hero" : "text-key"} leading-none ${tone(trip.r)}`}>
        {formatR(trip.r)}
      </div>
      <div className={`num text-value ${tone(trip.realized)}`}>{formatPnl(trip.realized)}</div>
      <StopVerdictLine verdict={verdict} />
      {trip.addedLower > 0 && (
        <p className="text-label text-muted-foreground">
          Added below your average while red <span className="num">&times;{trip.addedLower}</span>
        </p>
      )}
      <p className="num text-label text-muted-foreground">
        up to {trip.peakQty}x &middot; {px(trip.avgEntry)} to {px(trip.avgExit)}
        {trip.stopAssumed ? " · assumed stop" : ""}
        {trip.stopMoves > 0 ? ` · stop moved ×${trip.stopMoves}` : ""}
      </p>
    </section>
  );
}
