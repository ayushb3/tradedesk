import { Minus, Plus } from "lucide-react";

import { REPLAY_KEYS_HELP, useReplayKeys } from "@/lib/replayKeys";
import { MAX_SPEED, MIN_SPEED, formatSpeed, minuteLabel, stepSpeed } from "@/lib/replaySpeed";

/**
 * Replay speed: a compact "− 4x +" stepper over the powers of two, 0.5x to 64x
 * (chart-seconds per real second). ↑ doubles, ↓ halves; both are clamped at the ends and
 * never scroll the page.
 */
export function SpeedSlider({
  value,
  onChange,
  disabled,
  minuteTag,
  max = MAX_SPEED,
}: {
  value: number;
  onChange: (speed: number) => void;
  disabled?: boolean;
  /** Shown when the replay has to play minute bars; `reason` goes in the tooltip. */
  minuteTag?: { reason: string } | null;
  /** Top speed (default 64x; free replay and the author go to 1024x). */
  max?: number;
}) {
  // ↑ doubles, ↓ halves (the shared keymap; ignored while typing or with a modifier held).
  useReplayKeys({ onSpeed: (dir) => onChange(stepSpeed(value, dir, max)) }, !disabled);

  const btn =
    "flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40 disabled:hover:bg-transparent";
  return (
    <div className="flex items-center gap-1.5" title={`Speed. ${REPLAY_KEYS_HELP}`} data-testid="speed-control">
      <span className="label-caps text-label text-muted-foreground">Speed</span>
      <div className="flex items-center rounded-md border border-border" role="group" aria-label="Playback speed">
        <button
          type="button"
          className={btn}
          disabled={disabled || value <= MIN_SPEED}
          onClick={() => onChange(stepSpeed(value, -1, max))}
          aria-label="Slower (↓)"
          title="Half speed (↓)"
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
        <span className="num w-10 text-center text-value" aria-live="polite" aria-label={`${formatSpeed(value, max)}, ${minuteLabel(value, max)}`}>
          {formatSpeed(value, max)}
        </span>
        <button
          type="button"
          className={btn}
          disabled={disabled || value >= max}
          onClick={() => onChange(stepSpeed(value, 1, max))}
          aria-label="Faster (↑)"
          title="Double speed (↑)"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
      <span className="num hidden text-label text-muted-foreground lg:inline">{minuteLabel(value, max)}</span>
      {minuteTag && (
        <span
          className="num rounded border border-border px-1.5 text-label text-muted-foreground"
          title={`Playing minute bars: ${minuteTag.reason}`}
        >
          minute bars
        </span>
      )}
    </div>
  );
}
