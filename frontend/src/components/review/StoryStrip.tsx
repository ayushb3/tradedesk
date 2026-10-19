import { Skeleton } from "@/components/ui/skeleton";
import { storyStrip } from "@/lib/tradeReview";
import type { TradeExcursions } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  data: TradeExcursions | undefined;
  isLoading: boolean;
  isError: boolean;
}

/**
 * Story strip: best point, stop hit, worst point, after exit, read left to right. The one
 * place MAE/MFE-style numbers appear, in R, so "held a loser too long / exited early"
 * reads in a single row. The after-exit step carries no dollars.
 */
export function StoryStrip({ data, isLoading, isError }: Props) {
  if (isLoading) return <Skeleton className="h-20 w-full rounded-lg" />;
  const items = storyStrip(data);
  const suspect = data?.quality === "suspect_wicks";

  return (
    <section className="rounded-lg border border-border bg-background/40 p-4" data-testid="story-strip">
      {isError || !data ? (
        <p className="text-body text-muted-foreground">The story could not be loaded.</p>
      ) : !items ? (
        <p className="text-body text-muted-foreground">{data.reason ?? "Option bars unavailable"}. Fills are listed below.</p>
      ) : (
        <>
          <ol className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
            {items.map((it, i) => (
              <li key={it.key} className={cn("flex flex-col", it.estimated && "opacity-60")} data-estimated={it.estimated || undefined}>
                <span className="text-label label-caps text-muted-foreground">
                  {i > 0 && <span aria-hidden="true">→ </span>}
                  {it.label}
                </span>
                <span
                  className={cn(
                    "num text-value font-semibold",
                    it.key === "stop" && it.value !== "Stop not touched" && "text-stop",
                    it.key === "after" && "text-grey",
                  )}
                >
                  {it.value}
                </span>
                {it.sub && <span className="num text-label text-muted-foreground">{it.sub}</span>}
              </li>
            ))}
          </ol>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-label text-muted-foreground">
            <span className="label-caps">Times in {data.tz_label}</span>
            {data.estimated && <span className="text-stop">estimated premium (no traded bars)</span>}
            {suspect && (
              <span title={data.quality_reason ?? undefined} data-testid="thin-bars-note">
                estimated from thin bars
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}
