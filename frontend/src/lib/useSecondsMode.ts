/**
 * Seconds charts on a minute-bar player (day replay, free replay, group review): pick 5s/15s/30s
 * and the chart draws second bars built from trade prints, with real volume, for the playhead's
 * 30-minute chunk and the one before. A second-level clock rides on the minute engine, so play
 * reveals the bars second by second, ←/→ step a minute and Shift+←/→ step a second.
 *
 * The page keeps its minute engine (scrubber, sim, fills); this hook hands back a `replay`
 * whose play/step controls drive the seconds clock while a seconds interval is on.
 */
import { useMemo } from "react";

import { useReplayKeys } from "@/lib/replayKeys";
import { resSecOf, type SecondsRes } from "@/lib/secondsChart";
import { useSecondsChart, type SecondsChart } from "@/lib/useSecondsChart";
import { useSecondsClock, type SecondsClock } from "@/lib/useSecondsClock";
import type { ReplayEngine } from "@/lib/useReplayEngine";

export interface SecondsMode<R extends ReplayEngine> {
  /** A seconds interval is on. */
  active: boolean;
  res: SecondsRes | null;
  resSec: number;
  /** The second-level playhead (the minute's last second while off). */
  playSec: number | null;
  /** Active and inside a minute (offset < 59): minute-built values must stop at the last finished minute. */
  midMinute: boolean;
  /** Bar open of the playhead's second bar (active only). */
  barOpenSec: number | null;
  chart: SecondsChart;
  clock: SecondsClock;
  /** The page's replay with play/step routed to the seconds clock while active. */
  replay: R;
}

export function useSecondsMode<R extends ReplayEngine>({
  replay,
  res,
  symbol,
  minuteSec,
  speed,
  indexOfMinute,
  maxSpeed,
  keysEnabled = true,
}: {
  maxSpeed?: number;
  replay: R;
  res: SecondsRes | null;
  /** The underlying whose prints build the bars (SPXW is served from the SPY proxy). */
  symbol: string | null | undefined;
  /** The engine's current bar open (minute bars). */
  minuteSec: number | null;
  speed: number;
  indexOfMinute?: (minuteSec: number) => number;
  keysEnabled?: boolean;
}): SecondsMode<R> {
  const active = res !== null;
  const clock = useSecondsClock({
    enabled: active,
    minuteSec,
    index: replay.currentIndex,
    total: replay.total,
    setIndex: replay.setIndex,
    engineIsPlaying: replay.isPlaying,
    enginePause: replay.pause,
    speed,
    indexOfMinute,
    maxSpeed,
  });
  const chart = useSecondsChart({ symbol, playSec: clock.playSec, res, enabled: active });
  const resSec = res ? resSecOf(res) : 60;

  // While active the page's own keys are off (useReplay keyboardEnabled), and these drive the clock.
  useReplayKeys(
    {
      onToggle: clock.toggle,
      onStep: (dir, fine) => (fine ? clock.stepFine(dir) : clock.stepMinute(dir)),
    },
    active && keysEnabled,
  );

  const routed = useMemo<R>(() => {
    if (!active) return replay;
    return {
      ...replay,
      isPlaying: replay.isPlaying || clock.isPlaying,
      // The last minute still has seconds to play until its 59th; the first, until its 0th.
      atEnd: replay.atEnd && clock.offset >= 59,
      atStart: replay.atStart && clock.offset <= 0,
      restart: () => clock.skip(-1, Number.MAX_SAFE_INTEGER),
      play: clock.play,
      pause: () => {
        clock.pause();
        replay.pause();
      },
      toggle: clock.toggle,
      stepForward: () => clock.stepMinute(1),
      stepBack: () => clock.stepMinute(-1),
      stepFine: clock.stepFine,
    };
  }, [active, replay, clock]);

  return {
    active,
    res,
    resSec,
    playSec: clock.playSec,
    midMinute: active && clock.offset < 59,
    barOpenSec: active && clock.playSec != null ? Math.floor(clock.playSec / resSec) * resSec : null,
    chart,
    clock,
    replay: routed,
  };
}
