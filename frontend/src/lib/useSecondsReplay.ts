/**
 * Seconds-mode clock and data feed for 'Replay my trade'.
 *
 * Owns the playhead second, play/pause/steps and the keys (Space, left/right = one candle,
 * Shift+left/right = 1 s). The speed comes from the page's slider (chart-seconds per real
 * second); the clock advances by speed x elapsed time, not one second per timer tick. Second bars are polled from the server with the
 * CURRENT playhead as the `playhead` parameter and the newest held bar as `after`, so
 * the browser never holds a bar from after the playhead. A poll is in flight at most
 * once; if the playhead has moved on when it lands, the next one follows at once.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Candle } from "@/lib/api";
import { isInputFocused } from "@/lib/useReplay";
import { advance, CLOCK_TICK_MS } from "@/lib/replaySpeed";
import {
  arrowStep,
  candleStepSec,
  clampSec,
  mergeBars,
  pollDue,
  stepSec,
} from "@/lib/secondsReplay";

interface Options {
  enabled: boolean;
  tradeId: number | null;
  lead: number;
  /** First and last playable second (epoch). */
  minSec: number;
  maxSec: number;
  /** Chart-seconds per real second. */
  speed: number;
  /** Second-bar resolution; a change refetches from the start of the window. */
  res?: "1s" | "5s";
}

export function useSecondsReplay({ enabled, tradeId, lead, minSec, maxSec, speed, res = "1s" }: Options) {
  const [playSec, setPlaySec] = useState(minSec);
  const [isPlaying, setIsPlaying] = useState(false);
  const [contract, setContract] = useState<Candle[]>([]);
  const [underlying, setUnderlying] = useState<Candle[]>([]);
  const [error, setError] = useState<string | null>(null);

  // What has been fetched, per trade+lead. Held in refs so a poll can read the latest.
  const fetchedUpTo = useRef<number | null>(null);
  const inflight = useRef(false);
  const heldContract = useRef<Candle[]>([]);
  const heldUnderlying = useRef<Candle[]>([]);
  const key = `${tradeId}:${lead}:${res}`;
  const tradeKey = `${tradeId}:${lead}`;
  const keyRef = useRef(key);
  const tradeKeyRef = useRef(tradeKey);
  const [tick, setTick] = useState(0); // re-runs the poll effect when a poll lands

  useEffect(() => {
    if (keyRef.current === key) return;
    keyRef.current = key;
    fetchedUpTo.current = null;
    heldContract.current = [];
    heldUnderlying.current = [];
    setContract([]);
    setUnderlying([]);
    setError(null);
    // A new trade stops playback; a resolution change keeps playing.
    if (tradeKeyRef.current !== tradeKey) {
      tradeKeyRef.current = tradeKey;
      setIsPlaying(false);
    }
  }, [key, tradeKey]);

  const atStart = playSec <= minSec;
  const atEnd = playSec >= maxSec;

  // ---- the clock: speed x elapsed real time, whole seconds, remainder carried
  const speedRef = useRef(speed);
  speedRef.current = speed;
  useEffect(() => {
    if (!enabled || !isPlaying) return;
    let last = performance.now();
    let carry = 0;
    const id = window.setInterval(() => {
      const now = performance.now();
      const r = advance(carry, now - last, speedRef.current, 1);
      last = now;
      carry = r.carry;
      if (r.steps > 0) setPlaySec((s) => Math.min(s + r.steps, maxSec));
    }, CLOCK_TICK_MS);
    return () => window.clearInterval(id);
  }, [enabled, isPlaying, maxSec]);
  useEffect(() => {
    if (isPlaying && atEnd) setIsPlaying(false);
  }, [isPlaying, atEnd]);

  // ---- the feed: one poll at a time, always at the current playhead
  useEffect(() => {
    if (!enabled || tradeId === null || inflight.current) return;
    if (!pollDue(fetchedUpTo.current, playSec)) return;
    inflight.current = true;
    const asked = playSec;
    const myKey = key;
    api
      .replayTradeSeconds(tradeId, {
        playhead: new Date(asked * 1000).toISOString(),
        // everything up to the last poll's playhead is already held
        after: fetchedUpTo.current === null ? null : new Date(fetchedUpTo.current * 1000).toISOString(),
        lead,
        res,
      })
      .then((r) => {
        if (keyRef.current !== myKey) return;
        if (!r.available) {
          setError(r.reason ?? "second bars unavailable");
          fetchedUpTo.current = asked;
          return;
        }
        setError(null);
        heldContract.current = mergeBars(heldContract.current, r.contract);
        heldUnderlying.current = mergeBars(heldUnderlying.current, r.underlying);
        fetchedUpTo.current = asked;
        setContract(heldContract.current);
        setUnderlying(heldUnderlying.current);
      })
      .catch(() => {
        if (keyRef.current === myKey) setError("second bars could not be loaded");
      })
      .finally(() => {
        inflight.current = false;
        setTick((n) => n + 1);
      });
  }, [enabled, tradeId, lead, res, playSec, key, tick]);

  // ---- controls
  const seek = useCallback((sec: number) => setPlaySec(clampSec(sec, minSec, maxSec)), [minSec, maxSec]);
  const pause = useCallback(() => setIsPlaying(false), []);
  const play = useCallback(() => {
    setPlaySec((s) => (s >= maxSec ? minSec : s));
    setIsPlaying(true);
  }, [minSec, maxSec]);
  const toggle = useCallback(() => (isPlaying ? pause() : play()), [isPlaying, pause, play]);
  const step = useCallback(
    (delta: number) => {
      setIsPlaying(false);
      setPlaySec((s) => stepSec(s, delta, minSec, maxSec));
    },
    [minSec, maxSec],
  );
  /** One candle (lands on its last second, so it is complete). */
  const stepCandle = useCallback(
    (dir: 1 | -1) => {
      setIsPlaying(false);
      setPlaySec((s) => candleStepSec(s, dir, minSec, maxSec));
    },
    [minSec, maxSec],
  );
  const stepForward = useCallback(() => stepCandle(1), [stepCandle]);
  const stepBack = useCallback(() => stepCandle(-1), [stepCandle]);
  /** The fine step: one second. */
  const stepFine = useCallback((dir: 1 | -1) => step(dir), [step]);
  const restart = useCallback(() => {
    setIsPlaying(false);
    setPlaySec(minSec);
  }, [minSec]);

  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (isInputFocused(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === " ") {
        e.preventDefault();
        toggle();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const a = arrowStep(e.key, e.shiftKey);
        if (a.fine) step(a.dir);
        else stepCandle(a.dir);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, toggle, step, stepCandle]);

  return {
    playSec, isPlaying, atStart, atEnd, error,
    contract, underlying,
    seek, play, pause, toggle, stepForward, stepBack, stepFine, restart,
  };
}
