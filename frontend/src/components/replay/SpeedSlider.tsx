import { useEffect, useRef } from "react";

import { isInputFocused } from "@/lib/useReplay";
import {
  MAX_SPEED,
  MIN_SPEED,
  SLIDER_STEPS,
  SPEED_NOTCHES,
  formatSpeed,
  minuteLabel,
  posToSpeed,
  snapSpeed,
  speedToPos,
  stepNotch,
} from "@/lib/replaySpeed";

/**
 * Replay speed: one continuous log-scale slider, 0.5x to 100x (chart-seconds per real
 * second). Snaps to the notches unless Shift is held while dragging; `[` and `]` step a
 * notch. Below 60x the page plays second bars when it can; `minuteTag` says when it can't.
 */
export function SpeedSlider({
  value,
  onChange,
  disabled,
  minuteTag,
}: {
  value: number;
  onChange: (speed: number) => void;
  disabled?: boolean;
  /** Shown when a sub-60x speed has to play minute bars; `reason` goes in the tooltip. */
  minuteTag?: { reason: string } | null;
}) {
  const shift = useRef(false);
  const latest = useRef({ value, onChange });
  latest.current = { value, onChange };

  // [ and ] step a notch (ignored while typing or with a modifier held).
  useEffect(() => {
    if (disabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isInputFocused(e.target)) return;
      if (e.key !== "[" && e.key !== "]") return;
      e.preventDefault();
      const { value: v, onChange: set } = latest.current;
      set(stepNotch(v, e.key === "]" ? 1 : -1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [disabled]);

  const trackShift = (e: { shiftKey: boolean }) => {
    shift.current = e.shiftKey;
  };

  return (
    <div className="flex items-center gap-2" title="Speed: drag (Shift: no snapping), or [ and ]">
      <span className="label-caps text-label text-muted-foreground">Speed</span>
      <div className="relative flex h-5 w-28 items-center">
        {SPEED_NOTCHES.map((n) => (
          <span
            key={n}
            aria-hidden
            className="pointer-events-none absolute top-0 h-1.5 w-px bg-muted-foreground/40"
            style={{ left: `${(speedToPos(n) / SLIDER_STEPS) * 100}%` }}
          />
        ))}
        <input
          type="range"
          min={0}
          max={SLIDER_STEPS}
          step={1}
          value={speedToPos(value)}
          disabled={disabled}
          onPointerDown={trackShift}
          onPointerMove={trackShift}
          onKeyDown={trackShift}
          onKeyUp={trackShift}
          onChange={(e) => {
            const raw = posToSpeed(Number(e.target.value));
            onChange(shift.current ? raw : snapSpeed(raw));
          }}
          className="relative h-1 w-full cursor-pointer accent-primary disabled:opacity-50"
          aria-label="Playback speed"
          aria-valuemin={MIN_SPEED}
          aria-valuemax={MAX_SPEED}
          aria-valuenow={value}
          aria-valuetext={`${formatSpeed(value)}, ${minuteLabel(value)}`}
        />
      </div>
      <span className="num w-10 text-value">{formatSpeed(value)}</span>
      <span className="num hidden text-label text-muted-foreground lg:inline">{minuteLabel(value)}</span>
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
