import type { ReactNode } from "react";

import { ExcursionReadout } from "@/components/review/ExcursionReadout";
import { FillTable } from "@/components/review/FillTable";
import { ReviewRail } from "@/components/review/ReviewRail";
import { StoryStrip } from "@/components/review/StoryStrip";
import { VerdictBand } from "@/components/review/VerdictBand";
import type { Excursion, ReviewFill, ReviewOption, Trade, TradeExcursions } from "@/lib/api";
import type { DayTally } from "@/lib/tradeReview";

interface Props {
  trade: Trade;
  label: string;
  tally: DayTally | null;
  fills: ReviewFill[];
  option: ReviewOption | null;
  /** Share trades' MFE/MAE (basis: underlying); option trades use the story strip. */
  excursion: Excursion | null;
  excursions: {
    data: TradeExcursions | undefined;
    isLoading: boolean;
    isError: boolean;
  };
  onOpenMore: () => void;
  onReplay: () => void;
  replaying?: boolean;
  /** The Critique section, rendered last when present. */
  critique?: ReactNode;
}

/**
 * The review side panel: result, rule checks, story, the rail (call, plan, tags, lesson, note,
 * More, Replay), fills and the critique. Sections are separated by hairlines; the shell scrolls it.
 */
export function ReviewPanel({
  trade,
  label,
  tally,
  fills,
  option,
  excursion,
  excursions,
  onOpenMore,
  onReplay,
  replaying,
  critique,
}: Props) {
  return (
    <div className="flex flex-col" data-testid="review-panel">
      <VerdictBand trade={trade} label={label} tally={tally} compact />

      {option ? (
        <div className="border-b border-border p-2">
          <StoryStrip data={excursions.data} isLoading={excursions.isLoading} isError={excursions.isError} narrow />
        </div>
      ) : (
        excursion && (
          <div className="border-b border-border p-2">
            <ExcursionReadout excursion={excursion} />
          </div>
        )
      )}

      <ReviewRail
        trade={trade}
        excursions={excursions.data}
        onOpenMore={onOpenMore}
        onReplay={onReplay}
        replaying={replaying}
      />

      <section className="space-y-2 border-b border-border px-4 py-3">
        <h3 className="text-label label-caps text-muted-foreground">Fills</h3>
        <FillTable fills={fills} hoveredFillId={null} onHoverFill={() => {}} />
      </section>

      {critique && <div className="border-b border-border px-4 py-3">{critique}</div>}
    </div>
  );
}
