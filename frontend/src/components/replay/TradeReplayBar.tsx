import { Check, Pause, Play, SlidersHorizontal, StepBack, StepForward } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrubberMarks } from "@/components/replay/ReplayControlBar";
import { DISPLAY_TZ_LABEL } from "@/lib/format";
import { LADDERS, type Ladder, type StackIndicators } from "@/lib/stackIndicators";
import type { ReplayState, Speed } from "@/lib/useReplay";

export const LEADS = [5, 15, 30] as const;
const TRADE_SPEEDS: readonly Speed[] = [1, 2, 4];

const segment = (on: boolean) =>
  `num h-8 px-3 text-label first:rounded-l-md last:rounded-r-md border border-border -ml-px first:ml-0 ${
    on ? "bg-play/15 text-play border-play/50 relative z-10" : "text-muted-foreground hover:bg-accent"
  }`;

/**
 * Slim top bar for a trade replay: contract and date, lead, the big PT clock,
 * transport, speed and Exit. Everything else lives in the chart and the rails.
 */
export function TradeReplayBar({
  contract,
  date,
  lead,
  onLeadChange,
  clock,
  replay,
  disabled,
  onExit,
}: {
  contract: string;
  date: string;
  lead: number;
  onLeadChange: (lead: number) => void;
  /** "HH:MM" of the playhead bar in PT, or null while loading. */
  clock: string | null;
  replay: ReplayState;
  disabled: boolean;
  onExit: () => void;
}) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 border-b border-border px-4 py-2">
      <div className="min-w-0">
        <div className="num truncate text-value">{contract}</div>
        <div className="num text-label text-muted-foreground">{date}</div>
      </div>

      <div className="flex items-center gap-2">
        <span className="label-caps text-label text-muted-foreground">Lead</span>
        <div className="flex" role="group" aria-label="Minutes before entry">
          {LEADS.map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={lead === m}
              className={segment(lead === m)}
              onClick={() => onLeadChange(m)}
            >
              {m} min
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-baseline gap-2" aria-live="off">
        <span className="num text-key">{clock ?? "--:--"}</span>
        <span className="label-caps text-label text-muted-foreground">{DISPLAY_TZ_LABEL}</span>
      </div>

      <div className="flex items-center gap-1">
        <Button
          size="icon"
          variant="ghost"
          disabled={disabled || replay.atStart}
          onClick={replay.stepBack}
          aria-label="Step back (←)"
          title="Step back (←)"
        >
          <StepBack />
        </Button>
        <Button
          size="icon"
          variant={replay.isPlaying ? "default" : "outline"}
          disabled={disabled}
          onClick={replay.toggle}
          className="h-9 w-9"
          aria-label={replay.isPlaying ? "Pause (Space)" : "Play (Space)"}
          title={replay.isPlaying ? "Pause (Space)" : "Play (Space)"}
        >
          {replay.isPlaying ? <Pause /> : <Play />}
        </Button>
        <Button
          size="icon"
          variant="ghost"
          disabled={disabled || replay.atEnd}
          onClick={replay.stepForward}
          aria-label="Step forward (→)"
          title="Step forward (→)"
        >
          <StepForward />
        </Button>
      </div>

      <div className="flex" role="group" aria-label="Playback speed">
        {TRADE_SPEEDS.map((s) => (
          <button
            key={String(s)}
            type="button"
            aria-pressed={replay.speed === s}
            disabled={disabled}
            className={segment(replay.speed === s)}
            onClick={() => replay.setSpeed(s)}
          >
            {s}x
          </button>
        ))}
      </div>

      <Button variant="ghost" className="ml-auto text-muted-foreground" onClick={onExit}>
        Exit replay
      </Button>
    </div>
  );
}

/** Scrubber with the trade window marked, plus the indicator menu. */
export function TradeScrubber({
  replay,
  disabled,
  marks,
  indicators,
  onIndicatorsChange,
}: {
  replay: ReplayState;
  disabled: boolean;
  marks?: { entryIndex: number | null; exitIndex: number | null };
  indicators: StackIndicators;
  onIndicatorsChange: (v: StackIndicators) => void;
}) {
  return (
    <div className="pointer-events-auto absolute inset-x-3 bottom-2 z-10 flex items-center gap-3 rounded-md border border-border/50 bg-card/85 px-3 py-2 backdrop-blur">
      <div className="relative flex h-5 flex-1 items-center">
        <ScrubberMarks marks={marks} total={replay.total} current={replay.currentIndex} />
        <input
          type="range"
          min={0}
          max={Math.max(0, replay.total - 1)}
          value={replay.currentIndex}
          disabled={disabled}
          onMouseDown={replay.pause}
          onChange={(e) => replay.setIndex(Number(e.target.value))}
          className="relative h-1 w-full cursor-pointer accent-primary"
          aria-label="Scrub replay position"
          aria-valuetext={replay.barLabel || undefined}
        />
      </div>
      <IndicatorsMenu value={indicators} onChange={onIndicatorsChange} />
    </div>
  );
}

function IndicatorsMenu({
  value,
  onChange,
}: {
  value: StackIndicators;
  onChange: (v: StackIndicators) => void;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" variant="ghost" className="gap-2 text-label" aria-label="Indicators">
          <SlidersHorizontal className="h-4 w-4" />
          Indicators
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-60 p-3" align="end" sideOffset={8}>
        <div className="flex flex-col gap-3">
          <Row label="VWAP" on={value.vwap} onClick={() => onChange({ ...value, vwap: !value.vwap })} />
          <div>
            <p className="label-caps mb-1 text-label text-muted-foreground">Levels on SPY</p>
            {(Object.keys(LADDERS) as Ladder[]).map((k) => (
              <Row
                key={k}
                label={LADDERS[k].label}
                hint={LADDERS[k].levels.map((l) => l.label).join(" ")}
                on={value.ladder === k}
                onClick={() => onChange({ ...value, ladder: k })}
              />
            ))}
          </div>
          <Row label="EMAs (grey)" on={value.ema} onClick={() => onChange({ ...value, ema: !value.ema })} />
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Row({
  label,
  hint,
  on,
  onClick,
}: {
  label: string;
  hint?: string;
  on: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-body hover:bg-accent"
    >
      <Check className={`h-4 w-4 ${on ? "opacity-100" : "opacity-0"}`} />
      <span>{label}</span>
      {hint && <span className="num ml-auto text-label text-muted-foreground">{hint}</span>}
    </button>
  );
}
