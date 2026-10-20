/**
 * Seconds-mode clock and data feed for 'Replay my trade'.
 *
 * Owns the playhead second, play/pause/speed/steps and the keys (Space, left/right =
 * 1 s, Shift+left/right = 1 min). Second bars are polled from the server with the
 * CURRENT playhead as the `playhead` parameter and the newest held bar as `after`, so
 * the browser never holds a bar from after the playhead. A poll is in flight at most
 * once; if the playhead has moved on when it lands, the next one follows at once.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Candle } from "@/lib/api";
import { isInputFocused } from "@/lib/useReplay";
import {
  arrowDelta,
  clampSec,
  mergeBars,
  pollDue,
  stepSec,
  tickMs,
  type SecondsSpeed,
} from "@/lib/secondsReplay";

interface Options {
  enabled: boolean;
  tradeId: number | null;
  lead: number;
  /** First and last playable second (epoch). */
  minSec: number;
  maxSec: number;
}

export function useSecondsReplay({ enabled, tradeId, lead, minSec, maxSec }: Options) {
  const [playSec, setPlaySec] = useState(minSec);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<SecondsSpeed>(1);
  const [contract, setContract] = useState<Candle[]>([]);
  const [underlying, setUnderlying] = useState<Candle[]>([]);
  const [error, setError] = useState<string | null>(null);

  // What has been fetched, per trade+lead. Held in refs so a poll can read the latest.
  const fetchedUpTo = useRef<number | null>(null);
  const inflight = useRef(false);
  const heldContract = useRef<Candle[]>([]);
  const heldUnderlying = useRef<Candle[]>([]);
  const key = `${tradeId}:${lead}`;
  const keyRef = useRef(key);
  const [tick, setTick] = useState(0); // re-runs the poll effect when a poll lands

  useEffect(() => {
    if (keyRef.current === key) return;
    keyRef.current = key;
    fetchedUpTo.current = null;
    heldContract.current = [];
    heldUnderlying.current = [];
    setContract([]);
    setUnderlying([]);
    setIsPlaying(false);
    setError(null);
  }, [key]);

  const atStart = playSec <= minSec;
  const atEnd = playSec >= maxSec;

  // ---- the clock
  useEffect(() => {
    if (!enabled || !isPlaying) return;
    const id = window.setInterval(() => setPlaySec((s) => Math.min(s + 1, maxSec)), tickMs(speed));
    return () => window.clearInterval(id);
  }, [enabled, isPlaying, speed, maxSec]);
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
  }, [enabled, tradeId, lead, playSec, key, tick]);

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
  const stepForward = useCallback(() => step(1), [step]);
  const stepBack = useCallback(() => step(-1), [step]);
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
        step(arrowDelta(e.key, e.shiftKey));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, toggle, step]);

  return {
    playSec, isPlaying, speed, setSpeed, atStart, atEnd, error,
    contract, underlying,
    seek, play, pause, toggle, stepForward, stepBack, restart,
  };
}
