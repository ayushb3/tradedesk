import { Check, ChevronLeft, ChevronRight, SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrubberMarks } from "@/components/replay/ReplayControlBar";
import { ReplayTransport } from "@/components/replay/ReplayTransport";
import { SpeedSlider } from "@/components/replay/SpeedSlider";
import { DISPLAY_TZ_LABEL } from "@/lib/format";
import { LADDERS, type Ladder, type StackIndicators } from "@/lib/stackIndicators";
import type { ReplayState } from "@/lib/useReplay";

export const LEADS = [5, 15, 30] as const;

const segment = (on: boolean) =>
  `num h-7 px-2.5 text-label first:rounded-l-md last:rounded-r-md border border-border -ml-px first:ml-0 ${
    on ? "bg-play/15 text-play border-play/50 relative z-10" : "text-muted-foreground hover:bg-accent"
  }`;

/**
 * Slim top bar for a trade (or day) replay: prev / contract / next, lead, the speed slider,
 * the PT clock, transport, the collapsed day-so-far summary and Exit. One short row so the
 * charts keep the height; everything else lives in the chart and the rail.
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
  speed,
  onSpeedChange,
  minuteTag,
  nav,
  daySoFar,
}: {
  contract: string;
  date: string;
  /** Minutes before entry; omitted on a day replay (no lead control). */
  lead?: number;
  onLeadChange?: (lead: number) => void;
  /** "HH:MM" (or "HH:MM:SS" on second bars) of the playhead in PT, or null while loading. */
  clock: string | null;
  replay: ReplayState;
  disabled: boolean;
  onExit: () => void;
  speed: number;
  onSpeedChange: (speed: number) => void;
  /** Set when a sub-60x speed is playing minute bars (with the reason). */
  minuteTag?: { reason: string } | null;
  /** Previous / next trade (same day in time order, then the adjacent day). */
  nav?: { onPrev: (() => void) | null; onNext: (() => void) | null };
  /** The collapsed rule bar (day so far, cutoff, call source). */
  daySoFar?: React.ReactNode;
}) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-1">
      <div className="flex min-w-0 items-center gap-1" title={date ? `Session ${date}` : undefined}>
        {nav && (
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            disabled={!nav.onPrev}
            onClick={nav.onPrev ?? undefined}
            aria-label="Previous trade"
            title="Previous trade"
          >
            <ChevronLeft />
          </Button>
        )}
        <span className="num truncate text-value">{contract}</span>
        {nav && (
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            disabled={!nav.onNext}
            onClick={nav.onNext ?? undefined}
            aria-label="Next trade"
            title="Next trade"
          >
            <ChevronRight />
          </Button>
        )}
      </div>

      {lead !== undefined && onLeadChange && (
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
                {m}m
              </button>
            ))}
          </div>
        </div>
      )}

      <SpeedSlider value={speed} onChange={onSpeedChange} disabled={disabled} minuteTag={minuteTag} />

      <div className="flex items-baseline gap-2" aria-live="off">
        <span className="num text-value font-semibold">{clock ?? "--:--"}</span>
        <span className="label-caps text-label text-muted-foreground">{DISPLAY_TZ_LABEL}</span>
      </div>

      <ReplayTransport engine={replay} disabled={disabled} />

      {daySoFar}

      <Button variant="ghost" size="sm" className="ml-auto text-muted-foreground" onClick={onExit}>
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
    <div className="pointer-events-auto absolute inset-x-3 bottom-1 z-10 flex items-center gap-3 rounded-md border border-border/50 bg-card/85 px-3 py-0.5 backdrop-blur">
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
        <Button size="sm" variant="ghost" className="h-7 gap-2 text-label" aria-label="Indicators">
          <SlidersHorizontal className="h-4 w-4" />
          Indicators
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-3" align="end" sideOffset={8}>
        <div className="flex flex-col gap-3">
          <Row label="VWAP" hint="white" on={value.vwap} onClick={() => onChange({ ...value, vwap: !value.vwap })} />
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
          <Row label="EMA 9 / 21 / 50" hint="green, dark red, light blue" on={value.ema} onClick={() => onChange({ ...value, ema: !value.ema })} />
          <Row label="SMA 200 / 400" hint="orange, red dots" on={value.sma} onClick={() => onChange({ ...value, sma: !value.sma })} />
          <Row
            label="Caller levels"
            hint="blue, 1h 200 SMA red"
            on={value.caller}
            onClick={() => onChange({ ...value, caller: !value.caller })}
          />
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
