/**
 * ReplayControlBar — floating control strip anchored to the bottom of the chart area.
 *
 * Consolidates the previous Scrubber + ReplayControls into a single component.
 * Receives a ReplayState from useReplay() and renders:
 *   Row 1: [Restart] [StepBack] [Play/Pause] [StepForward] │ scrubber │ [HH:MM ET] │ speed │ timeframe │ [Indicators]
 *   Row 2: centered bar counter (barLabel)
 *
 * The component owns no playback logic — all state and actions live in useReplay.
 */
import { useState } from "react";
import {
  Check,
  Eye,
  EyeOff,
  Pause,
  Play,
  RotateCcw,
  SlidersHorizontal,
  StepBack,
  StepForward,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SPEEDS, speedLabel, type ReplayState, type Speed } from "@/lib/useReplay";
import { REPLAY_TIMEFRAMES, type IndicatorSeriesMap } from "@/lib/api";
import {
  DEFAULT_REPLAY_CONFIG,
  type ReplayConfig,
} from "@/lib/replayConfig";
import { overlaySpecs, LEVEL_META } from "@/components/replay/ReplayChart";

interface ReplayControlBarProps {
  replay: ReplayState;
  disabled?: boolean;
  config: ReplayConfig;
  onConfigChange: (c: ReplayConfig) => void;
  indicators?: IndicatorSeriesMap;
}

export function ReplayControlBar({
  replay,
  disabled = false,
  config,
  onConfigChange,
  indicators,
}: ReplayControlBarProps) {
  const off = disabled || replay.total === 0;

  return (
    <div
      className="
        pointer-events-auto
        absolute inset-x-3 bottom-2 z-10
        flex flex-col gap-1
        rounded-md border border-border/50 bg-card/85
        px-3 py-2
        backdrop-blur
      "
    >
      {/* Row 1: transport + scrubber + jump-to-time + speed + timeframe + indicators */}
      <div className="flex items-center gap-1.5">
        {/* Restart */}
        <Button
          size="icon"
          variant="ghost"
          disabled={off || (replay.atStart && !replay.isPlaying)}
          onClick={replay.restart}
          aria-label="Restart (returns to bar 1)"
          title="Restart (returns to bar 1)"
        >
          <RotateCcw />
        </Button>

        {/* Step Back */}
        <Button
          size="icon"
          variant="ghost"
          disabled={off || replay.atStart}
          onClick={replay.stepBack}
          aria-label="Step back (←)"
          title="Step back (←)"
        >
          <StepBack />
        </Button>

        {/* Play / Pause — hero control, slightly larger */}
        <Button
          size="icon"
          variant={replay.isPlaying ? "default" : "ghost"}
          disabled={off}
          onClick={replay.toggle}
          className="h-9 w-9"
          aria-label={replay.isPlaying ? "Pause (Space)" : "Play (Space)"}
          title={replay.isPlaying ? "Pause" : "Play"}
        >
          {replay.isPlaying ? <Pause /> : <Play />}
        </Button>

        {/* Step Forward */}
        <Button
          size="icon"
          variant="ghost"
          disabled={off || replay.atEnd}
          onClick={replay.stepForward}
          aria-label="Step forward (→)"
          title="Step forward (→)"
        >
          <StepForward />
        </Button>

        {/* Divider */}
        <div className="mx-1 h-4 w-px bg-border" />

        {/* Scrubber */}
        <input
          type="range"
          min={0}
          max={Math.max(0, replay.total - 1)}
          value={replay.currentIndex}
          disabled={off}
          onMouseDown={replay.pause}
          onChange={(e) => replay.setIndex(Number(e.target.value))}
          className="h-1 flex-1 cursor-pointer accent-primary"
          aria-label="Scrub replay position"
          aria-valuetext={replay.barLabel || undefined}
        />

        {/* Divider */}
        <div className="mx-1 h-4 w-px bg-border" />

        {/* Jump-to-time input + ET label */}
        <JumpToTimeInput replay={replay} disabled={off} />

        {/* Divider */}
        <div className="mx-1 h-4 w-px bg-border" />

        {/* Speed Select */}
        <Select
          value={String(replay.speed)}
          onValueChange={(v) =>
            replay.setSpeed((v === "max" ? "max" : Number(v)) as Speed)
          }
          disabled={off}
        >
          <SelectTrigger
            className="h-7 w-[68px] tabular"
            aria-label="Playback speed"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SPEEDS.map((s) => (
              <SelectItem key={String(s)} value={String(s)} className="tabular">
                {speedLabel(s)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Timeframe Select — triggers refetch via queryKey upstream */}
        <Select
          value={config.timeframe}
          onValueChange={(v) =>
            onConfigChange({ ...config, timeframe: v as typeof config.timeframe })
          }
        >
          <SelectTrigger
            className="h-7 w-[58px] tabular"
            aria-label="Candle timeframe"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {REPLAY_TIMEFRAMES.map((tf) => (
              <SelectItem key={tf} value={tf} className="tabular">
                {tf}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Indicators Popover */}
        <IndicatorsPopover
          config={config}
          onConfigChange={onConfigChange}
          indicators={indicators}
        />
      </div>

      {/* Row 2: bar counter — only shown when we have a label */}
      {replay.barLabel && (
        <div className="flex justify-center">
          <span
            className="font-mono tabular-nums text-[11px] text-muted-foreground"
            aria-live="polite"
          >
            {replay.barLabel}
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Jump-to-time sub-component — isolated so it owns its own input state.
// ---------------------------------------------------------------------------

function JumpToTimeInput({
  replay,
  disabled,
}: {
  replay: ReplayState;
  disabled: boolean;
}) {
  const [value, setValue] = useState("");
  const [noMatch, setNoMatch] = useState(false);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = e.target.value;
    setValue(v);
    // Clear the red ring as soon as the user edits the field.
    if (noMatch) setNoMatch(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      const matched = replay.jumpToTime(value);
      if (!matched) setNoMatch(true);
      else setNoMatch(false);
    }
  }

  return (
    <div className="flex items-center gap-1">
      <input
        type="text"
        value={value}
        placeholder="--:--"
        disabled={disabled}
        maxLength={5}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        className={[
          "w-16 rounded-md border bg-transparent px-2 py-1 text-[11px] tabular-nums",
          "placeholder:text-muted-foreground/50",
          "focus:outline-none focus:ring-1 focus:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
          noMatch ? "ring-1 ring-destructive" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        aria-label="Jump to time (ET)"
        title="Jump to time (ET) — type HH:MM and press Enter"
      />
      <span className="text-[10px] text-muted-foreground">ET</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Indicators Popover — visibility toggles + param inputs.
// ---------------------------------------------------------------------------

interface IndicatorsPopoverProps {
  config: ReplayConfig;
  onConfigChange: (c: ReplayConfig) => void;
  indicators?: IndicatorSeriesMap;
}

function IndicatorsPopover({
  config,
  onConfigChange,
  indicators,
}: IndicatorsPopoverProps) {
  // EMA periods edit state (local string until committed)
  const [emaDraft, setEmaDraft] = useState<string>(
    config.emaPeriods.join(", "),
  );

  function toggleOverlay(key: string) {
    const next = new Set(config.hiddenOverlays);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onConfigChange({ ...config, hiddenOverlays: next });
  }

  function toggleLevel(key: string) {
    const next = new Set(config.hiddenLevels);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onConfigChange({ ...config, hiddenLevels: next });
  }

  function toggleRsi() {
    onConfigChange({ ...config, rsiHidden: !config.rsiHidden });
  }

  function commitEma() {
    const parsed = emaDraft
      .split(",")
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => !isNaN(n) && n > 0);
    if (parsed.length > 0) {
      onConfigChange({ ...config, emaPeriods: parsed });
    }
  }

  function handleRsiPeriod(e: React.ChangeEvent<HTMLInputElement>) {
    const v = parseInt(e.target.value, 10);
    if (!isNaN(v) && v > 0) onConfigChange({ ...config, rsiPeriod: v });
  }

  function handleOrMinutes(e: React.ChangeEvent<HTMLInputElement>) {
    const v = parseInt(e.target.value, 10);
    if (!isNaN(v) && v > 0) onConfigChange({ ...config, orMinutes: v });
  }

  // Derive overlay specs from whatever the session returned (or empty).
  const specs = overlaySpecs(indicators ?? {});

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 gap-1.5 px-2 text-xs"
          aria-label="Indicator settings"
          title="Indicator settings"
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Indicators</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-64 p-3"
        align="end"
        sideOffset={8}
      >
        <div className="flex flex-col gap-3">
          {/* Header */}
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Indicators
            </span>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-[10px]"
              onClick={() => {
                onConfigChange(DEFAULT_REPLAY_CONFIG);
                setEmaDraft(DEFAULT_REPLAY_CONFIG.emaPeriods.join(", "));
              }}
            >
              Reset
            </Button>
          </div>

          {/* Overlay toggles */}
          {specs.length > 0 && (
            <section>
              <p className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground/70">
                Overlays
              </p>
              <div className="flex flex-col gap-1">
                {specs.map((o) => {
                  const on = !config.hiddenOverlays.has(o.key);
                  return (
                    <ToggleRow
                      key={o.key}
                      label={o.label}
                      on={on}
                      color={o.color}
                      onToggle={() => toggleOverlay(o.key)}
                    />
                  );
                })}
              </div>
            </section>
          )}

          {/* Level toggles */}
          <section>
            <p className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground/70">
              Levels
            </p>
            <div className="flex flex-col gap-1">
              {LEVEL_META.map((lv) => {
                const on = !config.hiddenLevels.has(lv.key);
                return (
                  <ToggleRow
                    key={lv.key}
                    label={lv.label}
                    on={on}
                    color={lv.color}
                    onToggle={() => toggleLevel(lv.key)}
                  />
                );
              })}
            </div>
          </section>

          {/* RSI toggle */}
          <section>
            <p className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground/70">
              Subpane
            </p>
            <ToggleRow
              label="RSI"
              on={!config.rsiHidden}
              color="#eab308"
              onToggle={toggleRsi}
            />
          </section>

          <div className="h-px bg-border" />

          {/* Param inputs — trigger refetch upstream */}
          <section>
            <p className="mb-1.5 text-[10px] uppercase tracking-wide text-muted-foreground/70">
              Parameters (refetch)
            </p>
            <div className="flex flex-col gap-2">
              {/* EMA periods */}
              <div className="flex items-center justify-between gap-2">
                <label className="text-xs text-muted-foreground">
                  EMA periods
                </label>
                <input
                  type="text"
                  value={emaDraft}
                  onChange={(e) => setEmaDraft(e.target.value)}
                  onBlur={commitEma}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      commitEma();
                      e.currentTarget.blur();
                    }
                  }}
                  className="w-28 rounded border border-border bg-transparent px-2 py-0.5 text-right text-xs tabular-nums focus:outline-none focus:ring-1 focus:ring-ring"
                  placeholder="9, 21, 50, 200"
                  aria-label="EMA periods (comma-separated)"
                />
              </div>

              {/* RSI period */}
              <div className="flex items-center justify-between gap-2">
                <label className="text-xs text-muted-foreground">
                  RSI period
                </label>
                <input
                  type="number"
                  min={2}
                  max={100}
                  value={config.rsiPeriod}
                  onChange={handleRsiPeriod}
                  className="w-16 rounded border border-border bg-transparent px-2 py-0.5 text-right text-xs tabular-nums focus:outline-none focus:ring-1 focus:ring-ring"
                  aria-label="RSI period"
                />
              </div>

              {/* OR minutes */}
              <div className="flex items-center justify-between gap-2">
                <label className="text-xs text-muted-foreground">
                  OR window (min)
                </label>
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={config.orMinutes}
                  onChange={handleOrMinutes}
                  className="w-16 rounded border border-border bg-transparent px-2 py-0.5 text-right text-xs tabular-nums focus:outline-none focus:ring-1 focus:ring-ring"
                  aria-label="Opening range window in minutes"
                />
              </div>
            </div>
          </section>
        </div>
      </PopoverContent>
    </Popover>
  );
}

// ---------------------------------------------------------------------------
// ToggleRow — a compact row with an eye icon toggle and a color swatch.
// ---------------------------------------------------------------------------

function ToggleRow({
  label,
  on,
  color,
  onToggle,
}: {
  label: string;
  on: boolean;
  color: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-accent focus:outline-none focus:ring-1 focus:ring-ring"
      aria-pressed={on}
    >
      {/* Color swatch */}
      <span
        className="inline-block h-2 w-2 shrink-0 rounded-full"
        style={{ background: color }}
      />
      {/* Label */}
      <span
        className={on ? "text-popover-foreground" : "text-muted-foreground line-through"}
      >
        {label}
      </span>
      {/* Eye icon */}
      <span className="ml-auto">
        {on ? (
          <Eye className="h-3 w-3 text-muted-foreground" />
        ) : (
          <EyeOff className="h-3 w-3 text-muted-foreground/40" />
        )}
      </span>
      {/* Checkmark for screen readers / clarity */}
      {on && <Check className="sr-only" />}
    </button>
  );
}
