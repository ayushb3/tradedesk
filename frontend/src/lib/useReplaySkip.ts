/**
 * Skip for a player: `[` / `]` and the Skip buttons move one whole candle of the chart's
 * interval and land on its close (lib/replaySkip). The page says where the playhead is, in
 * which unit it moves (60 on a minute player, 1 on a seconds clock), and how to seek.
 */
import { useCallback } from "react";

import { useReplayKeys } from "@/lib/replayKeys";
import { sizeLabel, skipTargetSec } from "@/lib/replaySkip";

export interface ReplaySkip {
  skip: (dir: 1 | -1) => void;
  /** "5m", "15s": the candle size a skip moves. */
  skipLabel: string;
}

export function useReplaySkip({
  posSec,
  candleSec,
  unitSec,
  seek,
  enabled = true,
}: {
  /** The playhead: a bar open on a minute player, the second on a seconds clock. */
  posSec: number | null;
  /** The chart's candle size (its interval). */
  candleSec: number;
  unitSec: number;
  /** Move the playhead to this second (a bar open on a minute player); the caller pauses. */
  seek: (sec: number) => void;
  enabled?: boolean;
}): ReplaySkip | null {
  const bar = Math.max(candleSec, unitSec);
  const skip = useCallback(
    (dir: 1 | -1) => {
      if (posSec == null) return;
      // A minute player's playhead is the bar open; skip lands on the candle's last minute open.
      seek(skipTargetSec(posSec, bar, dir, unitSec));
    },
    [posSec, bar, unitSec, seek],
  );
  useReplayKeys({ onSkip: skip }, enabled && posSec != null);
  return enabled ? { skip, skipLabel: sizeLabel(bar) } : null;
}
