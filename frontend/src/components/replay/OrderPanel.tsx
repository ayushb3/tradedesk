import { Button } from "@/components/ui/button";
import { ResultCard } from "@/components/replay/ResultCard";
import type { Candle } from "@/lib/api";
import { formatClock, formatPnl, formatR } from "@/lib/format";
import {
  MULTIPLIER,
  openR,
  sessionRealized,
  totalPnl,
  unrealized,
  type SimFill,
  type SimState,
} from "@/lib/simPosition";

export const MIN_QTY = 1;
export const MAX_QTY = 99;

export interface QtyUnit {
  one: string;
  many: string;
}
const CONTRACT_UNIT: QtyUnit = { one: "contract", many: "contracts" };

interface Props {
  /** Premium bar at the playhead; null before data loads. */
  candle: Candle | null;
  /** Premium bars, for the stop verdict of finished round trips. */
  bars: Candle[];
  state: SimState;
  /** Contract symbol shown in the orders table. */
  symbol: string;
  /** False when there are no contract bars to trade against. */
  enabled: boolean;
  qty: number;
  onQtyChange: (qty: number) => void;
  onBuy: (qty: number) => void;
  onSell: (qty: number) => void;
  onCloseAll: () => void;
  logError: string | null;
  /** What one unit of quantity is called; defaults to option contracts. */
  unit?: QtyUnit;
  /** Show the Greeks line (options only; free replay trades the underlying). */
  greeks?: boolean;
  /** Shown while trading is off; defaults to the trade-replay "no option bars" note. */
  disabledNote?: string;
}

const tone = (n: number) =>
  n > 0 ? "text-up" : n < 0 ? "text-down" : "text-foreground";
const px = (n: number) => `$${n.toFixed(2)}`;
const clampQty = (n: number) =>
  Math.min(MAX_QTY, Math.max(MIN_QTY, Math.round(n) || MIN_QTY));

/**
 * Practice order ticket. Every fill is the in-app practice sim at the playhead bar's close;
 * nothing here is ever sent to a broker.
 */
export function OrderPanel({
  candle,
  bars,
  state,
  symbol,
  enabled,
  qty,
  onQtyChange,
  onBuy,
  onSell,
  onCloseAll,
  logError,
  unit = CONTRACT_UNIT,
  greeks = true,
  disabledNote = "No option bars for this trade, so there is no premium to trade.",
}: Props) {
  const { position: p, trips } = state;
  const mark = candle?.close ?? null;
  const open = p.qty > 0;
  const r = mark !== null ? openR(p, mark) : null;
  const openPnl = mark !== null ? unrealized(p, mark) : 0;
  const dayPnl =
    sessionRealized(state) + (mark !== null ? totalPnl(p, mark) : p.realized);
  const canAct = enabled && candle !== null;
  const addedLowerCount = p.fills.filter((f) => f.addedLower).length;
  const orders: SimFill[] = trips
    .flatMap((t) => t.fills)
    .concat(p.fills)
    .reverse();

  return (
    <div className="flex flex-col gap-2 px-3 py-2">
      <div>
        <div className="label-caps text-label font-semibold text-muted-foreground">
          Practice orders
        </div>
        <p className="text-label text-muted-foreground">
          TradeDesk only, never sent to a broker
        </p>
      </div>

      <dl className="num grid grid-cols-[auto_1fr] gap-x-3 text-label">
        <dt className="text-muted-foreground">Open P&amp;L</dt>
        <dd className={`text-right ${tone(openPnl)}`}>{formatPnl(openPnl)}</dd>
        <dt className="text-muted-foreground">Day P&amp;L</dt>
        <dd className={`text-right ${tone(dayPnl)}`}>{formatPnl(dayPnl)}</dd>
      </dl>

      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-1.5">
        <Button
          size="sm"
          disabled={!canAct}
          onClick={() => onBuy(qty)}
          className="bg-up text-white hover:bg-up/90"
        >
          Buy market
        </Button>
        <div className="num flex items-center rounded border border-border text-label">
          <button
            type="button"
            aria-label="Decrease quantity"
            className="px-1.5 py-1 hover:bg-muted disabled:opacity-40"
            disabled={qty <= MIN_QTY}
            onClick={() => onQtyChange(clampQty(qty - 1))}
          >
            &minus;
          </button>
          <span className="min-w-6 text-center" aria-label="Quantity">
            {qty}
          </span>
          <button
            type="button"
            aria-label="Increase quantity"
            className="px-1.5 py-1 hover:bg-muted disabled:opacity-40"
            disabled={qty >= MAX_QTY}
            onClick={() => onQtyChange(clampQty(qty + 1))}
          >
            +
          </button>
        </div>
        <Button
          size="sm"
          disabled={!canAct || !open}
          onClick={() => onSell(qty)}
          className="bg-orange-500 text-white hover:bg-orange-500/90"
        >
          Sell market
        </Button>
      </div>
      <Button
        size="sm"
        variant="outline"
        disabled={!canAct || !open}
        onClick={onCloseAll}
      >
        Flatten
      </Button>
      {!enabled && (
        <p className="text-label text-muted-foreground">
          {disabledNote}
        </p>
      )}

      {open ? (
        <dl className="num grid grid-cols-[auto_1fr] gap-x-3 text-label">
          <dt className="text-muted-foreground">Open</dt>
          <dd className="text-right">
            {p.qty} {p.qty === 1 ? unit.one : unit.many} @ {px(p.avg)}
          </dd>
          <dt className="text-muted-foreground">Mark</dt>
          <dd className="text-right">{mark !== null ? px(mark) : "-"}</dd>
          <dt className="text-muted-foreground">
            Stop{p.stopAssumed ? " (assumed)" : ""}
          </dt>
          <dd className="text-right text-stop">
            {p.stop !== null ? px(p.stop) : "-"}
          </dd>
          {r !== null && (
            <>
              <dt className="text-muted-foreground">Open R</dt>
              <dd className={`text-right ${tone(r)}`}>{formatR(r)}</dd>
            </>
          )}
          {p.realized !== 0 && (
            <>
              <dt className="text-muted-foreground">Realized</dt>
              <dd className={`text-right ${tone(p.realized)}`}>
                {formatPnl(p.realized)}
              </dd>
            </>
          )}
          <dt className="text-muted-foreground">1R</dt>
          <dd className="text-right">{p.risk !== null ? px(p.risk) : "-"}</dd>
        </dl>
      ) : (
        <p className="text-label text-muted-foreground">
          Flat. Buy at the playhead close to start a round trip.
        </p>
      )}
      {addedLowerCount > 0 && (
        <p className="text-label text-muted-foreground">
          Added below your average while red:{" "}
          <span className="num">{addedLowerCount}</span>
        </p>
      )}

      {greeks && (
        <p className="text-label text-muted-foreground">
          Greeks: not available (no option quotes in history)
        </p>
      )}

      <section aria-label="Recent orders" className="space-y-1">
        <div className="label-caps text-label font-semibold text-muted-foreground">
          Recent orders
        </div>
        {orders.length === 0 ? (
          <p className="text-label text-muted-foreground">
            No practice orders yet.
          </p>
        ) : (
          <table className="w-full text-label">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="font-normal">Symbol</th>
                <th className="font-normal">Status</th>
                <th className="font-normal">Side</th>
                <th className="text-right font-normal">Qty</th>
                <th className="text-right font-normal">Avg fill</th>
                <th className="text-right font-normal">Time</th>
              </tr>
            </thead>
            <tbody className="num">
              {orders.map((f, i) => (
                <tr key={`${f.ts}-${i}`}>
                  <td className="max-w-20 truncate pr-1" title={symbol}>
                    {symbol || "-"}
                  </td>
                  <td>
                    <span className="rounded-full bg-muted px-1.5 text-label text-muted-foreground">
                      Filled
                    </span>
                  </td>
                  <td className={f.side === "buy" ? "text-up" : "text-down"}>
                    {f.side === "buy" ? "Buy" : "Sell"}
                  </td>
                  <td className="text-right">{f.qty}</td>
                  <td className="text-right">{f.price.toFixed(2)}</td>
                  <td className="text-right">{formatClock(f.ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {trips.length > 0 && (
        <section className="space-y-2">
          <div className="label-caps text-label font-semibold text-muted-foreground">
            Round trips ({trips.length}) ·{" "}
            <span className={`num ${tone(sessionRealized(state))}`}>
              {formatPnl(sessionRealized(state))}
            </span>
          </div>
          <div className="space-y-2">
            {/* Newest first; only the newest gets the hero size. */}
            {trips
              .map((t, i) => (
                <ResultCard
                  key={i}
                  trip={t}
                  index={i}
                  bars={bars}
                  hero={i === trips.length - 1}
                />
              ))
              .reverse()}
          </div>
        </section>
      )}
      {logError && <p className="text-label text-loss">{logError}</p>}
      <p className="text-label text-muted-foreground">
        <Kbd>Shift+B</Kbd> buy &middot; <Kbd>Shift+S</Kbd> sell &middot;{" "}
        <Kbd>Shift+Alt+F</Kbd> flatten
      </p>
      <p className="text-label text-muted-foreground">
        <Kbd>Space</Kbd> play/pause &middot; <Kbd>&larr;</Kbd>/<Kbd>&rarr;</Kbd> step &middot;{" "}
        <Kbd>Shift</Kbd>+arrow 1s &middot; <Kbd>&uarr;</Kbd>/<Kbd>&darr;</Kbd> speed
      </p>
      <p className="text-label text-muted-foreground">
        1R = (avg - stop) x {MULTIPLIER} at your first buy.
      </p>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="num rounded border border-border bg-muted px-1 text-label">
      {children}
    </kbd>
  );
}
