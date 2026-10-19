import { useEffect, useState } from "react";
import { MoreHorizontal, Play, Plus } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import type { Trade, TradeExcursions } from "@/lib/api";
import { useCreateTag, usePatchTrade, useSetCallSource, useTags } from "@/lib/queries";
import { writtenBeforeEntry } from "@/lib/reviewChart";
import { cn } from "@/lib/utils";

interface Props {
  trade: Trade;
  excursions: TradeExcursions | null | undefined;
  onOpenMore: () => void;
}

/**
 * Trade review rail: Replay first, then what was written before entry, whose call it was, one
 * setup tag and one line of lesson. Everything else (grade, checklist, screenshots, the Debrief
 * chat) sits behind "More".
 */
export function ReviewRail({ trade, excursions, onOpenMore }: Props) {
  const navigate = useNavigate();
  const rows = writtenBeforeEntry(trade, excursions);

  return (
    <aside className="flex flex-col gap-4 rounded-lg border border-border bg-card/40 p-4" data-testid="review-rail">
      <Button size="lg" className="w-full gap-2" onClick={() => navigate(`/replay?trade=${trade.id}&lead=15`)}>
        <Play className="h-4 w-4" />
        Replay this trade
      </Button>

      <section className="space-y-2" data-testid="written-before-entry">
        <h3 className="text-label label-caps text-muted-foreground">Written before entry</h3>
        <dl className="space-y-1.5">
          {rows.map((r) => (
            <div key={r.key} className="flex items-baseline justify-between gap-3">
              <dt className="text-body text-muted-foreground">{r.label}</dt>
              <dd className={cn("num text-value", r.assumed && "text-stop")}>
                {r.value}
                {r.assumed && <span className="ml-1.5 text-label label-caps">assumed</span>}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <CallSource trade={trade} />
      <SetupTag trade={trade} />
      <Lesson trade={trade} />

      <Button variant="outline" className="w-full gap-2" onClick={onOpenMore}>
        <MoreHorizontal className="h-4 w-4" />
        More: grade, checklist, screenshots, debrief
      </Button>
    </aside>
  );
}

function CallSource({ trade }: { trade: Trade }) {
  const set = useSetCallSource();
  const source = trade.enrichment?.call_source ?? null;
  return (
    <section className="space-y-2">
      <h3 className="text-label label-caps text-muted-foreground">Whose call</h3>
      <div className="inline-flex overflow-hidden rounded-md border border-border" role="group" aria-label="Call source">
        {(["mentor", "own"] as const).map((v) => (
          <button
            key={v}
            type="button"
            disabled={set.isPending}
            aria-pressed={source === v}
            onClick={() => set.mutate({ id: trade.id, callSource: source === v ? null : v })}
            className={cn(
              "px-4 py-1.5 text-body capitalize transition-colors disabled:opacity-60",
              source === v ? "bg-play text-white" : "bg-transparent text-muted-foreground hover:bg-muted",
            )}
          >
            {v}
          </button>
        ))}
      </div>
    </section>
  );
}

/** One setup tag per trade: picking a tag replaces the previous one; tapping it again clears it. */
function SetupTag({ trade }: { trade: Trade }) {
  const { toast } = useToast();
  const { data: allTags } = useTags();
  const createTag = useCreateTag();
  const patch = usePatchTrade();
  const [newTag, setNewTag] = useState("");
  const current = trade.tags?.[0]?.id ?? null;

  const save = (tagIds: number[]) =>
    patch.mutate(
      { id: trade.id, body: { tag_ids: tagIds } },
      { onError: () => toast({ title: "Could not save the tag", variant: "destructive" }) },
    );

  const add = () => {
    const name = newTag.trim();
    if (!name || createTag.isPending) return;
    createTag.mutate(
      { name },
      {
        onSuccess: (tag) => {
          setNewTag("");
          save([tag.id]);
        },
        onError: () => toast({ title: "Could not create the tag", variant: "destructive" }),
      },
    );
  };

  return (
    <section className="space-y-2" data-testid="setup-tag">
      <h3 className="text-label label-caps text-muted-foreground">Setup</h3>
      <div className="flex flex-wrap gap-1.5">
        {(allTags ?? []).map((t) => (
          <button
            key={t.id}
            type="button"
            disabled={patch.isPending}
            aria-pressed={current === t.id}
            onClick={() => save(current === t.id ? [] : [t.id])}
            className={cn(
              "rounded-full border px-3 py-1 text-label font-medium transition-colors disabled:opacity-60",
              current === t.id
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {t.name}
          </button>
        ))}
        {(allTags ?? []).length === 0 && <span className="text-label text-muted-foreground">No setup tags yet.</span>}
      </div>
      <div className="flex gap-2">
        <Input
          value={newTag}
          onChange={(e) => setNewTag(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())}
          placeholder="New setup tag"
          aria-label="New setup tag"
          className="h-9 text-body"
        />
        <Button size="sm" variant="outline" onClick={add} disabled={!newTag.trim() || createTag.isPending} aria-label="Add tag">
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    </section>
  );
}

const oneLine = (s: string | null | undefined) => (s ?? "").replace(/\s*\n\s*/g, " ").trim();

/** One line of lesson, stored as the trade's note. Saved on Enter or when the field loses focus. */
function Lesson({ trade }: { trade: Trade }) {
  const { toast } = useToast();
  const patch = usePatchTrade();
  const saved = oneLine(trade.enrichment?.notes);
  const [text, setText] = useState(saved);
  useEffect(() => setText(oneLine(trade.enrichment?.notes)), [trade.id, trade.enrichment?.notes]);

  const commit = () => {
    const next = text.trim();
    if (next === saved) return; // unchanged: never rewrite a longer note
    patch.mutate(
      { id: trade.id, body: { notes: next } },
      { onError: () => toast({ title: "Could not save the lesson", variant: "destructive" }) },
    );
  };

  return (
    <section className="space-y-2">
      <label htmlFor="lesson" className="block text-label label-caps text-muted-foreground">
        Lesson, one line
      </label>
      <Input
        id="lesson"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            e.currentTarget.blur();
          }
        }}
        placeholder="What would you do again, or never again?"
        className="h-9 text-body"
      />
    </section>
  );
}
