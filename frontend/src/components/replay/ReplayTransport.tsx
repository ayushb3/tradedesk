import { ChevronsLeft, ChevronsRight, Pause, Play, RotateCcw, StepBack, StepForward } from "lucide-react";

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
  /** One whole candle of the chart's interval, landing on its close (`[` / `]`). */
  skip?: (dir: 1 | -1) => void;
  /** The candle size a skip moves ("5m"). */
  skipLabel?: string;
  /** The step size when it is not one minute (free replay on server 5m bars: "5m"). */
  stepLabel?: string;
}

/**
 * The one replay transport: [restart] · skip back · step back · play/pause · step · skip.
 * A step is one minute (Shift-click: one second where the player has second bars); a skip is
 * one whole candle of the chart's interval. Keys are bound by the player (lib/replayKeys),
 * so the titles name them.
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
  const stepSize = engine.stepLabel ?? "1m";
  const stepHint = engine.stepFine ? `${stepSize} (Shift: 1s)` : stepSize;
  const skipBtn = (dir: 1 | -1) =>
    engine.skip && engine.skipLabel ? (
      <Button
        size="sm"
        variant="ghost"
        className="h-8 gap-0.5 px-1.5 text-label"
        disabled={disabled || (dir > 0 ? engine.atEnd : engine.atStart)}
        onClick={() => engine.skip?.(dir)}
        aria-label={`Skip ${dir > 0 ? "forward" : "back"} ${engine.skipLabel} (${dir > 0 ? "]" : "["})`}
        title={`Skip ${engine.skipLabel} ${dir > 0 ? "forward" : "back"}: one whole candle, to its close (${dir > 0 ? "]" : "["})`}
        data-testid={dir > 0 ? "skip-forward" : "skip-back"}
      >
        {dir < 0 && <ChevronsLeft className="h-3.5 w-3.5" />}
        <span>Skip {engine.skipLabel}</span>
        {dir > 0 && <ChevronsRight className="h-3.5 w-3.5" />}
      </Button>
    ) : null;
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
      {skipBtn(-1)}
      <Button
        size="sm"
        variant="ghost"
        className="h-8 gap-0.5 px-1.5 text-label"
        disabled={disabled || engine.atStart}
        onClick={(e) => (e.shiftKey && engine.stepFine ? engine.stepFine(-1) : engine.stepBack())}
        aria-label={`Step back ${stepHint} (←)`}
        title={`Back ${stepHint} (←)`}
      >
        <StepBack className="h-3.5 w-3.5" />
        <span className="num">{stepSize}</span>
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
        size="sm"
        variant="ghost"
        className="h-8 gap-0.5 px-1.5 text-label"
        disabled={disabled || engine.atEnd}
        onClick={(e) => (e.shiftKey && engine.stepFine ? engine.stepFine(1) : engine.stepForward())}
        aria-label={`Step forward ${stepHint} (→)`}
        title={`Forward ${stepHint} (→)`}
      >
        <span className="num">{stepSize}</span>
        <StepForward className="h-3.5 w-3.5" />
      </Button>
      {skipBtn(1)}
    </div>
  );
}
