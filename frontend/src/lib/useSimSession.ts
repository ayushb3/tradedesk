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
}

/**
 * Sim state for trade-mode Replay. Fills are always at the playhead bar's close.
 * Each completed round trip is logged through POST /api/sim/trades. Quantities
 * are sent in shares (contracts x 100) because the sim endpoint prices with a
 * multiplier of 1, which makes its dollar P&L correct for options.
 */
export function useSimSession({ resetKey, symbol, sessionDate, writtenStop }: Options) {
  const [state, setState] = useState<SimState>(initialState);
  const stateRef = useRef(state);
  const [logError, setLogError] = useState<string | null>(null);
  const [logged, setLogged] = useState(0);
  const mutation = useCreateSimTrade();

  const apply = useCallback((next: SimState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const keyRef = useRef(resetKey);
  useEffect(() => {
    if (keyRef.current === resetKey) return;
    keyRef.current = resetKey;
    apply(initialState());
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
    (c: Candle | null) => {
      if (!c) return;
      apply(buy(stateRef.current, { price: c.close, ts: c.ts }, writtenStop));
    },
    [apply, writtenStop],
  );
  const onSell = useCallback(
    (c: Candle | null) => {
      if (!c) return;
      finish(stateRef.current, sell(stateRef.current, { price: c.close, ts: c.ts }));
    },
    [finish],
  );
  const onCloseAll = useCallback(
    (c: Candle | null) => {
      if (!c) return;
      finish(stateRef.current, closeAll(stateRef.current, { price: c.close, ts: c.ts }));
    },
    [finish],
  );
  const onSetStop = useCallback(
    (price: number, final = true) => apply(setStop(stateRef.current, price, final)),
    [apply],
  );

  return { state, onBuy, onSell, onCloseAll, onSetStop, logError, logged };
}
