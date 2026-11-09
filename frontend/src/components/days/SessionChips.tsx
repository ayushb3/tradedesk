import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { PlayCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { DeskPosition } from "@/lib/api";
import { groupReplayHref, sessionGroups } from "@/lib/groupReview";
import { useDeskRules } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** The most-traded ticker among these trades (the day replay plays one underlying). */
function groupSymbol(positions: DeskPosition[], ids: number[]): string | null {
  const n = new Map<string, number>();
  for (const p of positions) if (ids.includes(p.trade_id)) n.set(p.ticker, (n.get(p.ticker) ?? 0) + 1);
  let best: string | null = null;
  let most = 0;
  for (const [t, c] of n) {
    if (c > most) {
      best = t;
      most = c;
    }
  }
  return best;
}

/**
 * Group review: the day's trades as session chips, built from the trade times (lib/groupReview).
 * Pick one or more and "Review selected" plays just those trades in the day replay, from
 * 5 min before the first entry to 10 min after the last exit, with one combined P&L.
 */
export function SessionChips({ date, positions }: { date: string; positions: DeskPosition[] }) {
  const navigate = useNavigate();
  const rules = useDeskRules();
  const groups = sessionGroups(positions, rules.data);
  const [picked, setPicked] = useState<string[]>([]);
  if (groups.length === 0 || rules.isLoading) return null;

  const ids = groups.filter((g) => picked.includes(g.key)).flatMap((g) => g.tradeIds);
  const toggle = (key: string) =>
    setPicked((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]));

  return (
    <section className="flex flex-wrap items-center gap-2" aria-label="Review a session" data-testid="session-chips">
      <span className="label-caps text-label text-muted-foreground">Review a session</span>
      {groups.map((g) => {
        const on = picked.includes(g.key);
        return (
          <button
            key={g.key}
            type="button"
            aria-pressed={on}
            onClick={() => toggle(g.key)}
            title={`${g.label}: ${g.tradeIds.length} trade${g.tradeIds.length === 1 ? "" : "s"}, ${g.range} PT`}
            className={cn(
              "flex items-baseline gap-1.5 rounded-md border px-2 py-0.5 text-label transition-colors",
              on ? "border-play/50 bg-play/15 text-play" : "border-border text-muted-foreground hover:bg-accent",
            )}
          >
            <span className="font-medium">{g.label}</span>
            {g.range && <span className="num opacity-80">{g.range}</span>}
            <span className="num opacity-80">· {g.tradeIds.length}</span>
          </button>
        );
      })}
      <Button
        size="sm"
        variant="outline"
        className="gap-1.5"
        disabled={ids.length === 0}
        onClick={() => navigate(groupReplayHref(date, ids, groupSymbol(positions, ids)))}
        data-testid="review-selected"
      >
        <PlayCircle className="h-4 w-4" />
        Review selected{ids.length > 0 ? ` (${ids.length})` : ""}
      </Button>
    </section>
  );
}
