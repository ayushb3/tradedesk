/**
 * ActionButtons — the four big decision buttons shown at the decision bar.
 * Long / Short / Skip / Wait
 *
 * Wait means "one more bar then re-pause"; the caller handles advancing the
 * bar and looping back to the decision state.
 */

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type DrillChoice = "enter_long" | "enter_short" | "skip" | "wait";

interface ActionButtonsProps {
  onChoice: (choice: DrillChoice) => void;
  disabled?: boolean;
  className?: string;
}

export function ActionButtons({ onChoice, disabled, className }: ActionButtonsProps) {
  return (
    <div className={cn("flex flex-wrap items-center justify-center gap-3", className)}>
      <Button
        size="lg"
        className="min-w-[120px] bg-emerald-600 text-white hover:bg-emerald-500 focus-visible:ring-emerald-400"
        onClick={() => onChoice("enter_long")}
        disabled={disabled}
        aria-label="Enter Long"
      >
        Long
      </Button>
      <Button
        size="lg"
        className="min-w-[120px] bg-rose-600 text-white hover:bg-rose-500 focus-visible:ring-rose-400"
        onClick={() => onChoice("enter_short")}
        disabled={disabled}
        aria-label="Enter Short"
      >
        Short
      </Button>
      <Button
        size="lg"
        variant="outline"
        className="min-w-[120px]"
        onClick={() => onChoice("skip")}
        disabled={disabled}
        aria-label="Skip — no trade"
      >
        Skip
      </Button>
      <Button
        size="lg"
        variant="secondary"
        className="min-w-[120px]"
        onClick={() => onChoice("wait")}
        disabled={disabled}
        aria-label="Wait — one more bar"
        title="Defer the decision one bar, then re-pause"
      >
        Wait
      </Button>
    </div>
  );
}
