import { Button } from "@/components/ui/button";
import type { Candle } from "@/lib/api";
import { formatPnl, formatR } from "@/lib/format";
import {
  MULTIPLIER,
  openR,
  sessionRealized,
  totalPnl,
  unrealized,
  type SimState,
} from "@/lib/simPosition";

interface Props {
  /** Premium bar at the playhead; null before data loads. */
  candle: Candle | null;
  state: SimState;
  /** False when there are no contract bars to trade against. */
  enabled: boolean;
  onBuy: () => void;
  onSell: () => void;
  onCloseAll: () => void;
  logError: string | null;
}

const tone = (n: number) => (n > 0 ? "text-gain" : n < 0 ? "text-loss" : "text-foreground");
const px = (n: number) => `$${n.toFixed(2)}`;

/**
 * Buy / Sell against the playhead. Every press is 1 contract at the playhead
 * bar's close on the premium pane; there is no size or price field.
 */
export function SimPanel({ candle, state, enabled, onBuy, onSell, onCloseAll, logError }: Props) {
  const { position: p, trips } = state;
  const mark = candle?.close ?? null;
  const open = p.qty > 0;
  const r = mark !== null ? openR(p, mark) : null;
  const unreal = mark !== null ? unrealized(p, mark) : 0;
  const tripPnl = mark !== null ? totalPnl(p, mark) : p.realized;
  const canAct = enabled && candle !== null;
  const addedLowerCount = p.fills.filter((f) => f.addedLower).length;

  return (
    <div className="flex flex-col gap-4 border-b border-border p-3">
      <div className="label-caps text-label font-semibold text-muted-foreground">Sim</div>

      <div className="flex items-baseline gap-3">
        <span className="num text-key">{mark !== null ? px(mark) : "-"}</span>
        {open && r !== null && <span className={`num text-key ${tone(r)}`}>{formatR(r)}</span>}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Button
          disabled={!canAct}
          onClick={onBuy}
          className="bg-gain text-white hover:bg-gain/90"
        >
          Buy 1
        </Button>
        <Button
          disabled={!canAct || !open}
          onClick={onSell}
          className="bg-loss text-white hover:bg-loss/90"
        >
          Sell 1
        </Button>
      </div>
      <Button variant="outline" disabled={!canAct || !open} onClick={onCloseAll}>
        Close all
      </Button>
      {!enabled && (
        <p className="text-label text-muted-foreground">
          No option bars for this trade, so there is no premium to trade.
        </p>
      )}

      {open ? (
        <dl className="num grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-value">
          <dt className="text-label text-muted-foreground">Open</dt>
          <dd className="text-right">
            {p.qty} contract{p.qty === 1 ? "" : "s"}
          </dd>
          <dt className="text-label text-muted-foreground">Avg entry</dt>
          <dd className="text-right">{px(p.avg)}</dd>
          <dt className="text-label text-muted-foreground">
            Stop{p.stopAssumed ? " (assumed)" : ""}
          </dt>
          <dd className="text-right text-stop">{p.stop !== null ? px(p.stop) : "-"}</dd>
          <dt className="text-label text-muted-foreground">Open P&amp;L</dt>
          <dd className={`text-right ${tone(unreal)}`}>{formatPnl(unreal)}</dd>
          {p.realized !== 0 && (
            <>
              <dt className="text-label text-muted-foreground">Realized</dt>
              <dd className={`text-right ${tone(p.realized)}`}>{formatPnl(p.realized)}</dd>
            </>
          )}
          <dt className="text-label text-muted-foreground">Trip P&amp;L</dt>
          <dd className={`text-right ${tone(tripPnl)}`}>{formatPnl(tripPnl)}</dd>
          <dt className="text-label text-muted-foreground">1R</dt>
          <dd className="text-right">{p.risk !== null ? px(p.risk) : "-"}</dd>
        </dl>
      ) : (
        <p className="text-label text-muted-foreground">
          Flat. Buy 1 contract at the playhead close to start a round trip.
        </p>
      )}
      {addedLowerCount > 0 && (
        <p className="text-label text-muted-foreground">
          Added below your average while red: <span className="num">{addedLowerCount}</span>
        </p>
      )}

      {trips.length > 0 && (
        <section className="space-y-2">
          <div className="label-caps text-label font-semibold text-muted-foreground">
            Round trips ({trips.length}) ·{" "}
            <span className={`num ${tone(sessionRealized(state))}`}>
              {formatPnl(sessionRealized(state))}
            </span>
          </div>
          <ul className="space-y-2">
            {trips.map((t, i) => (
              <li key={i} className="rounded-md border border-border bg-card px-3 py-2">
                <div className="num flex items-baseline justify-between text-value">
                  <span className={tone(t.r)}>{formatR(t.r)}</span>
                  <span className={tone(t.realized)}>{formatPnl(t.realized)}</span>
                </div>
                <div className="num text-label text-muted-foreground">
                  #{i + 1} · up to {t.peakQty}x · {px(t.avgEntry)} to {px(t.avgExit)}
                  {t.stopAssumed ? " · assumed stop" : ""}
                  {t.addedLower > 0 ? ` · added lower x${t.addedLower}` : ""}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {logError && <p className="text-label text-loss">{logError}</p>}
      <p className="text-label text-muted-foreground">
        1R = (avg - stop) x {MULTIPLIER} at your first buy.
      </p>
    </div>
  );
}
