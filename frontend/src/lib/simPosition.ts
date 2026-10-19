/**
 * Pure position math for the Replay Buy/Sell sim (long premium only).
 *
 * One contract per press, filled at the playhead bar's close. A round trip is
 * one flat -> open -> flat cycle; a replay can hold several.
 *
 * Definitions (all money in dollars, one option contract = MULTIPLIER shares):
 *
 *  - Blended average entry: the qty-weighted mean price of every buy in the
 *    current round trip. A partial SELL realizes P&L at the average but does
 *    not change it (average-cost accounting). The average resets only when the
 *    position goes flat.
 *  - Realized P&L: sum over sells of (sell price - average at that moment) *
 *    MULTIPLIER * qty sold.
 *  - Unrealized P&L: (mark - average) * MULTIPLIER * open qty.
 *  - 1R (risk): FIXED AT THE FIRST BUY of the round trip:
 *        risk = (avg - stop) * MULTIPLIER * qty
 *    with avg, stop and qty as they are right after that first buy (with the
 *    1-contract press that is (entry - stop) * 100). Later adds, partial exits
 *    and dragging the stop do NOT change the denominator, so R stays comparable
 *    through the whole trip: adding to a loser shows up as a bigger loss in R,
 *    not as a re-based one.
 *  - R = (realized + unrealized) / risk while open; realized / risk for a
 *    finished round trip.
 *  - Stop: the replayed trade's written stop when it is a valid long stop (a
 *    positive price below the first fill), otherwise ASSUMED_STOP_FRAC (30%)
 *    below the first fill, flagged `stopAssumed`.
 *  - "Added lower": a buy while a position is open at a price strictly below the
 *    blended average (the position is red). Recorded per fill, never blocking.
 */

export const MULTIPLIER = 100;
export const ASSUMED_STOP_FRAC = 0.3;

export interface SimFill {
  side: "buy" | "sell";
  qty: number;
  price: number;
  /** ISO timestamp of the bar the fill happened on. */
  ts: string;
  /** Buys only: true when this add was below the blended average while red. */
  addedLower?: boolean;
}

/** The stop line in force from the bar AFTER `ts` (the playhead bar it was set on). */
export interface StopPoint {
  ts: string;
  stop: number;
}

export interface Position {
  /** Fills of the current round trip, in order. */
  fills: SimFill[];
  qty: number;
  /** Blended average entry; 0 while flat. */
  avg: number;
  /** Realized dollars in the current round trip. */
  realized: number;
  /** null while flat. */
  stop: number | null;
  stopAssumed: boolean;
  /** 1R in dollars, fixed at the first buy; null while flat. */
  risk: number | null;
  peakQty: number;
  stopMoves: number;
  /** Stop history: the first buy's stop, then each dragged stop on release. */
  stops: StopPoint[];
}

export interface RoundTrip {
  fills: SimFill[];
  avgEntry: number;
  avgExit: number;
  peakQty: number;
  realized: number;
  risk: number;
  /** realized / risk */
  r: number;
  /** The stop in force when the trip closed. */
  stop: number;
  stopAssumed: boolean;
  /** Number of adds below the blended average. */
  addedLower: number;
  stopMoves: number;
  /** Stop history, oldest first; the first entry is the stop at the first buy. */
  stops: StopPoint[];
}

export interface SimState {
  position: Position;
  trips: RoundTrip[];
}

export const flat = (): Position => ({
  fills: [],
  qty: 0,
  avg: 0,
  realized: 0,
  stop: null,
  stopAssumed: false,
  risk: null,
  peakQty: 0,
  stopMoves: 0,
  stops: [],
});

export const initialState = (): SimState => ({ position: flat(), trips: [] });

const round4 = (n: number) => Math.round(n * 10000) / 10000;

/** The stop for a first buy at `price`: the written stop if usable, else -30%. */
export function initialStop(
  price: number,
  writtenStop: number | null | undefined,
): { stop: number; assumed: boolean } {
  if (writtenStop != null && Number.isFinite(writtenStop) && writtenStop > 0 && writtenStop < price) {
    return { stop: writtenStop, assumed: false };
  }
  return { stop: round4(price * (1 - ASSUMED_STOP_FRAC)), assumed: true };
}

/** Buy `qty` at `price`. The first buy of a round trip fixes the stop and 1R. */
export function buy(
  state: SimState,
  fill: { price: number; ts: string; qty?: number },
  writtenStop?: number | null,
): SimState {
  const qty = fill.qty ?? 1;
  const p = state.position;
  if (!(qty > 0) || !(fill.price > 0)) return state;

  if (p.qty === 0) {
    const { stop, assumed } = initialStop(fill.price, writtenStop);
    const position: Position = {
      fills: [{ side: "buy", qty, price: fill.price, ts: fill.ts }],
      qty,
      avg: fill.price,
      realized: 0,
      stop,
      stopAssumed: assumed,
      risk: (fill.price - stop) * MULTIPLIER * qty,
      peakQty: qty,
      stopMoves: 0,
      stops: [{ ts: fill.ts, stop }],
    };
    return { ...state, position };
  }

  const addedLower = fill.price < p.avg;
  const newQty = p.qty + qty;
  const position: Position = {
    ...p,
    fills: [...p.fills, { side: "buy", qty, price: fill.price, ts: fill.ts, addedLower }],
    qty: newQty,
    avg: (p.avg * p.qty + fill.price * qty) / newQty,
    peakQty: Math.max(p.peakQty, newQty),
  };
  return { ...state, position };
}

/** Sell up to `qty` (default 1) at `price`. Going flat completes the round trip. */
export function sell(
  state: SimState,
  fill: { price: number; ts: string; qty?: number },
): SimState {
  const p = state.position;
  const qty = Math.min(fill.qty ?? 1, p.qty);
  if (p.qty === 0 || !(qty > 0)) return state;

  const realized = p.realized + (fill.price - p.avg) * MULTIPLIER * qty;
  const fills = [...p.fills, { side: "sell" as const, qty, price: fill.price, ts: fill.ts }];
  const left = p.qty - qty;

  if (left > 0) {
    return { ...state, position: { ...p, fills, qty: left, realized } };
  }
  return { position: flat(), trips: [...state.trips, finishTrip(p, fills, realized)] };
}

/** Sell whatever is left at `price`. */
export function closeAll(state: SimState, fill: { price: number; ts: string }): SimState {
  return sell(state, { ...fill, qty: state.position.qty });
}

/**
 * Move the stop line. Does not change 1R. During a drag pass `final = false`
 * for the in-between positions; the move is counted (and, when `ts` is given,
 * added to the stop history) once, on release.
 */
export function setStop(state: SimState, price: number, final = true, ts?: string): SimState {
  const p = state.position;
  if (p.qty === 0 || !(price > 0)) return state;
  return {
    ...state,
    position: {
      ...p,
      stop: price,
      stopAssumed: false,
      stopMoves: p.stopMoves + (final ? 1 : 0),
      stops: final && ts ? [...p.stops, { ts, stop: price }] : p.stops,
    },
  };
}

function finishTrip(p: Position, fills: SimFill[], realized: number): RoundTrip {
  const buys = fills.filter((f) => f.side === "buy");
  const sells = fills.filter((f) => f.side === "sell");
  const wavg = (xs: SimFill[]) =>
    xs.reduce((s, f) => s + f.price * f.qty, 0) / xs.reduce((s, f) => s + f.qty, 0);
  const risk = p.risk ?? 0;
  return {
    fills,
    avgEntry: wavg(buys),
    avgExit: wavg(sells),
    peakQty: p.peakQty,
    realized,
    risk,
    r: risk > 0 ? realized / risk : 0,
    stop: p.stop ?? 0,
    stopAssumed: p.stopAssumed,
    addedLower: buys.filter((f) => f.addedLower).length,
    stopMoves: p.stopMoves,
    stops: p.stops,
  };
}

/** Open P&L in dollars at `mark` (0 while flat). */
export function unrealized(p: Position, mark: number): number {
  return p.qty > 0 ? (mark - p.avg) * MULTIPLIER * p.qty : 0;
}

/** Realized + unrealized dollars of the current round trip. */
export function totalPnl(p: Position, mark: number): number {
  return p.realized + unrealized(p, mark);
}

/** Open R at `mark`: (realized + unrealized) / 1R; null while flat. */
export function openR(p: Position, mark: number): number | null {
  if (p.qty === 0 || p.risk == null || !(p.risk > 0)) return null;
  return totalPnl(p, mark) / p.risk;
}

/** Realized dollars over all finished round trips. */
export function sessionRealized(s: SimState): number {
  return s.trips.reduce((sum, t) => sum + t.realized, 0);
}
