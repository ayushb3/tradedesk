import { Pause, Play, RotateCcw, StepBack, StepForward } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  SPEEDS,
  speedLabel,
  type ReplayEngine,
  type Speed,
} from "@/lib/useReplayEngine";

interface ReplayControlsProps {
  engine: ReplayEngine;
  disabled?: boolean;
}

/** Real M2 replay transport: restart / step-back / play-pause / step-forward + speed. */
export function ReplayControls({ engine, disabled = false }: ReplayControlsProps) {
  const off = disabled || engine.total === 0;

  return (
    <div className="flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1">
      <Button
        size="icon"
        variant="ghost"
        disabled={off || engine.atStart}
        onClick={engine.restart}
        aria-label="Restart"
        title="Restart"
      >
        <RotateCcw />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        disabled={off || engine.atStart}
        onClick={engine.stepBack}
        aria-label="Step back"
        title="Step back"
      >
        <StepBack />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        disabled={off}
        onClick={engine.toggle}
        aria-label={engine.isPlaying ? "Pause" : "Play"}
        title={engine.isPlaying ? "Pause" : "Play"}
      >
        {engine.isPlaying ? <Pause /> : <Play />}
      </Button>
      <Button
        size="icon"
        variant="ghost"
        disabled={off || engine.atEnd}
        onClick={engine.stepForward}
        aria-label="Step forward"
        title="Step forward"
      >
        <StepForward />
      </Button>

      <div className="mx-1 h-5 w-px bg-border" />

      <Select
        value={String(engine.speed)}
        onValueChange={(v) =>
          engine.setSpeed((v === "max" ? "max" : Number(v)) as Speed)
        }
        disabled={off}
      >
        <SelectTrigger
          className="h-7 w-[76px] tabular"
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
    </div>
  );
}
