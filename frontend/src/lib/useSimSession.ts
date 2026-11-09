import { useCallback, useEffect, useRef, useState } from "react";

import type { Candle, CreateSimTradeRequest } from "@/lib/api";
import { useCreateSimTrade } from "@/lib/queries";
import {
  buy,
  closeAll,
  initialState,
  MULTIPLIER,
  sell,
  setStop,
  setTarget,
  type RoundTrip,
  type SimState,
} from "@/lib/simPosition";

interface Options {
  /** Resets the sim when it changes (trade id + lead). */
  resetKey: string;
  /** Option symbol, used when logging a finished round trip. */
  symbol: string;
  sessionDate: string;
  /** The replayed trade's written stop, if any. */
  writtenStop: number | null;
  /** How far below the first buy the stop is assumed when there is no written stop (default 30%). */
  assumedStopFrac?: number;
  /** Option pricing: buys fill at close + halfSpread, sells at close - halfSpread (min 0.01), with fees. */
  pricing?: { halfSpread: number; feePerContract: number };
}

/**
 * Sim state for trade-mode Replay. Fills are always at the playhead bar's close.
 * Each completed round trip is logged through POST /api/sim/trades. Quantities
 * are sent in shares (contracts x 100) because the sim endpoint prices with a
 * multiplier of 1, which makes its dollar P&L correct for options.
 */
const r2 = (n: number) => Math.round(n * 100) / 100;

export function useSimSession({
  resetKey,
  symbol,
  sessionDate,
  writtenStop,
  assumedStopFrac,
  pricing,
}: Options) {
  const halfSpread = pricing?.halfSpread;
  const feePerContract = pricing?.feePerContract;
  const buyPx = useCallback(
    (close: number) => (halfSpread === undefined ? close : r2(close + halfSpread)),
    [halfSpread],
  );
  const sellPx = useCallback(
    (close: number) => (halfSpread === undefined ? close : Math.max(0.01, r2(close - halfSpread))),
    [halfSpread],
  );
  const [state, setState] = useState<SimState>(initialState);
  const stateRef = useRef(state);
  const [logError, setLogError] = useState<string | null>(null);
  const [logged, setLogged] = useState(0);
  const mutation = useCreateSimTrade();

  const apply = useCallback((next: SimState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  // The key the current state belongs to: it lags `resetKey` by one commit (the reset is an
  // effect), so callers can skip reacting to the previous session's state.
  const [stateKey, setStateKey] = useState(resetKey);
  const keyRef = useRef(resetKey);
  useEffect(() => {
    if (keyRef.current === resetKey) return;
    keyRef.current = resetKey;
    apply(initialState());
    setStateKey(resetKey);
    setLogError(null);
    setLogged(0);
  }, [resetKey, apply]);

  const persist = useCallback(
    (trip: RoundTrip) => {
      const leg = (f: RoundTrip["fills"][number]) => ({
        ts: f.ts,
        qty: f.qty * MULTIPLIER,
        price: f.price,
      });
      const body: CreateSimTradeRequest = {
        symbol,
        direction: "long",
        session_date: sessionDate,
        entries: trip.fills.filter((f) => f.side === "buy").map(leg),
        exits: trip.fills.filter((f) => f.side === "sell").map(leg),
        stop_price: trip.stop,
      };
      mutation.mutate(body, {
        onSuccess: () => {
          setLogged((n) => n + 1);
          setLogError(null);
        },
        onError: () => setLogError("Round trip kept here, but it could not be logged."),
      });
    },
    [mutation, symbol, sessionDate],
  );

  const finish = useCallback(
    (prev: SimState, next: SimState) => {
      apply(next);
      if (next.trips.length > prev.trips.length) persist(next.trips[next.trips.length - 1]);
    },
    [apply, persist],
  );

  const onBuy = useCallback(
    (c: Candle | null, qty = 1) => {
      if (!c) return;
      apply(
        buy(
          stateRef.current,
          { price: buyPx(c.close), ts: c.ts, qty, feePerContract },
          writtenStop,
          assumedStopFrac,
        ),
      );
    },
    [apply, writtenStop, assumedStopFrac, buyPx, feePerContract],
  );
  const onSell = useCallback(
    (c: Candle | null, qty = 1) => {
      if (!c) return;
      finish(stateRef.current, sell(stateRef.current, { price: sellPx(c.close), ts: c.ts, qty, feePerContract }));
    },
    [finish, sellPx, feePerContract],
  );
  const onCloseAll = useCallback(
    (c: Candle | null) => {
      if (!c) return;
      finish(stateRef.current, closeAll(stateRef.current, { price: sellPx(c.close), ts: c.ts, feePerContract }));
    },
    [finish, sellPx, feePerContract],
  );
  /** Loss-rule force close at `price` (spread applied here); the trip is marked ruleClosed. */
  const onRuleClose = useCallback(
    (price: number, ts: string) => {
      const prev = stateRef.current;
      const next = closeAll(prev, { price: sellPx(price), ts, feePerContract });
      if (next.trips.length > prev.trips.length) {
        const trips = next.trips.slice();
        trips[trips.length - 1] = { ...trips[trips.length - 1], ruleClosed: true };
        finish(prev, { ...next, trips });
      }
    },
    [finish, sellPx, feePerContract],
  );
  // The move is stamped with the playhead bar it was made on, so the stop
  // verdict knows which stop was in force at each later bar.
  const playTsRef = useRef<string | undefined>(undefined);
  const setPlayTs = useCallback((ts: string | null) => {
    playTsRef.current = ts ?? undefined;
  }, []);
  const onSetStop = useCallback(
    (price: number, final = true) =>
      apply(setStop(stateRef.current, price, final, playTsRef.current)),
    [apply],
  );

  const onSetTarget = useCallback(
    (price: number | null) => apply(setTarget(stateRef.current, price)),
    [apply],
  );

  return { state, stateKey, onBuy, onSell, onCloseAll, onRuleClose, onSetStop, onSetTarget, setPlayTs, logError, logged };
}
