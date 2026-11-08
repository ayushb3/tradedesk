/**
 * The one trade screen: a bar on top, the chart on the left and one side panel on the right.
 * Review, replay and the Learn drill all use it, so the chart side looks and behaves the same
 * everywhere; only the panel changes (review: caller, plan, rule checks, notes; replay: the
 * practice order panel; Learn: the drill question).
 */
import type { ReactNode } from "react";

export function TradeScreen({
  bar,
  chart,
  panel,
  panelLabel,
  panelWidth = "w-80",
}: {
  /** The top bar (navigation, clock, transport). */
  bar?: ReactNode;
  /** The chart side: the chart plus anything under it (transport, scrubber). Fills the height. */
  chart: ReactNode;
  /** The side panel; scrolls on its own. */
  panel: ReactNode;
  /** Accessible name for the panel, e.g. "Practice orders". */
  panelLabel: string;
  /** Tailwind width class for the panel. */
  panelWidth?: string;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="trade-screen">
      {bar}
      <div className="flex min-h-0 flex-1">
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">{chart}</div>
        <aside
          aria-label={panelLabel}
          className={`hidden shrink-0 overflow-y-auto border-l border-border bg-background md:block ${panelWidth}`}
          data-testid="trade-screen-panel"
        >
          {panel}
        </aside>
      </div>
    </div>
  );
}
