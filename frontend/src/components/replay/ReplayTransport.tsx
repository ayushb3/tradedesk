import { Pause, Play, RotateCcw, StepBack, StepForward } from "lucide-react";

import { Button } from "@/components/ui/button";

/** What the transport drives: any player's engine (trade/day/free replay, the scenario author). */
export interface TransportEngine {
  isPlaying: boolean;
  atStart: boolean;
  atEnd: boolean;
  toggle: () => void;
  stepForward: () => void;
  stepBack: () => void;
  /** One second (Shift-click), where the player has second bars. */
  stepFine?: (dir: 1 | -1) => void;
  restart?: () => void;
}

/**
 * The one replay transport: [restart] back · play/pause · forward. A step is one candle;
 * Shift-click steps one second when the player has second bars. Keys are bound by the player
 * (lib/replayKeys), so the titles name them.
 */
export function ReplayTransport({
  engine,
  disabled = false,
  showRestart = false,
}: {
  engine: TransportEngine;
  disabled?: boolean;
  showRestart?: boolean;
}) {
  const stepHint = engine.stepFine ? "1 candle (Shift: 1 sec)" : "1 candle";
  return (
    <div className="flex items-center gap-1" role="group" aria-label="Playback">
      {showRestart && engine.restart && (
        <Button
          size="icon"
          variant="ghost"
          className="h-8 w-8"
          disabled={disabled || engine.atStart}
          onClick={engine.restart}
          aria-label="Restart"
          title="Restart"
        >
          <RotateCcw />
        </Button>
      )}
      <Button
        size="icon"
        variant="ghost"
        className="h-8 w-8"
        disabled={disabled || engine.atStart}
        onClick={(e) => (e.shiftKey && engine.stepFine ? engine.stepFine(-1) : engine.stepBack())}
        aria-label={`Step back ${stepHint} (←)`}
        title={`Back ${stepHint} (←)`}
      >
        <StepBack />
      </Button>
      <Button
        size="icon"
        variant={engine.isPlaying ? "default" : "outline"}
        disabled={disabled}
        onClick={engine.toggle}
        className="h-8 w-8"
        aria-label={engine.isPlaying ? "Pause (Space)" : "Play (Space)"}
        title={engine.isPlaying ? "Pause (Space)" : "Play (Space)"}
      >
        {engine.isPlaying ? <Pause /> : <Play />}
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="h-8 w-8"
        disabled={disabled || engine.atEnd}
        onClick={(e) => (e.shiftKey && engine.stepFine ? engine.stepFine(1) : engine.stepForward())}
        aria-label={`Step forward ${stepHint} (→)`}
        title={`Forward ${stepHint} (→)`}
      >
        <StepForward />
      </Button>
    </div>
  );
}
