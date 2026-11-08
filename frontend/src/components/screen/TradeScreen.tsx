/**
 * The one trade screen: a bar on top, the chart on the left and one side panel on the right.
 * Review, replay and the Learn drill all use it, so the chart side looks and behaves the same
 * everywhere; only the panel changes (review: caller, plan, rule checks, notes; replay: the
 * practice order panel; Learn: the drill question).
 */
import type { ReactNode } from "react";

/** Tailwind needs literal class names: the panel widths in use, applied from md up. */
const MD_WIDTH: Record<string, string> = { "w-72": "md:w-72", "w-80": "md:w-80", "w-[360px]": "md:w-[360px]" };

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
      {/* Below md the panel stacks under the chart (the page scrolls) instead of disappearing. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-hidden">
        <div className="relative flex min-h-[70vh] min-w-0 flex-1 flex-col md:min-h-0">{chart}</div>
        <aside
          aria-label={panelLabel}
          className={`w-full min-w-0 shrink-0 overflow-x-hidden border-t border-border bg-background md:overflow-y-auto md:border-l md:border-t-0 ${MD_WIDTH[panelWidth] ?? "md:w-80"}`}
          data-testid="trade-screen-panel"
        >
          {panel}
        </aside>
      </div>
    </div>
  );
}
