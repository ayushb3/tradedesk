import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import {
  ApiError,
  type ChecklistItem,
  type PatchTradeRequest,
  type Trade,
  type TradeGrade,
} from "@/lib/api";
import { formatNumber, formatR, pnlColorClass } from "@/lib/format";
import { useCreateTag, usePatchTrade, useTags } from "@/lib/queries";
import { cn } from "@/lib/utils";

const GRADES: TradeGrade[] = ["A", "B", "C"];

/** Seeded when a trade has no checklist yet. */
const DEFAULT_CHECKLIST: ChecklistItem[] = [
  "Followed my entry rules",
  "Size within plan",
  "Stop set before entry",
  "Aligned with thesis / trend",
  "No revenge / FOMO entry",
].map((item) => ({ item, checked: false }));

interface TradeEnrichmentPanelProps {
  trade: Trade;
  /**
   * Render the panel's own inline Save button. Default true (Review page).
   * The Journal drawer passes false and drives save via the imperative handle
   * from its own DrawerFooter, so its footer layout is unchanged.
   */
  showSaveButton?: boolean;
  /**
   * Fired whenever the in-flight (PATCH) state changes. Lets a host that renders
   * its own Save button (the Journal drawer footer) reflect saving state
   * reactively — reading `isSaving` off the imperative ref during render does
   * not re-render the host.
   */
  onSavingChange?: (saving: boolean) => void;
}

/** Imperative handle so a host (the drawer footer) can trigger save. */
export interface TradeEnrichmentPanelHandle {
  save: () => void;
  isSaving: boolean;
}

/**
 * The editable journal layer for a trade: tags, grade, stop, rule checklist,
 * notes, screenshots. Seeds from `trade.enrichment`, PATCHes on save, and
 * reflects the recomputed R. Rendered on the Trade Review page (cockpit).
 */
export const TradeEnrichmentPanel = forwardRef<
  TradeEnrichmentPanelHandle,
  TradeEnrichmentPanelProps
>(function TradeEnrichmentPanel(
  { trade, showSaveButton = true, onSavingChange },
  ref,
) {
  const { toast } = useToast();
  const patch = usePatchTrade();
  const { data: allTags } = useTags();
  const createTag = useCreateTag();

  const [notes, setNotes] = useState("");
  const [grade, setGrade] = useState<TradeGrade | null>(null);
  const [stop, setStop] = useState("");
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [screenshots, setScreenshots] = useState<string[]>([]);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [newTag, setNewTag] = useState("");
  const [newShot, setNewShot] = useState("");

  // (Re)seed the form whenever a different trade loads, or after a save returns
  // the recomputed trade. Keyed on id so background refetches don't clobber edits.
  useEffect(() => {
    const enr = trade.enrichment;
    setNotes(enr?.notes ?? "");
    setGrade(enr?.grade ?? null);
    setStop(trade.stop_price != null ? String(trade.stop_price) : "");
    setChecklist(
      enr?.checklist && enr.checklist.length > 0
        ? enr.checklist.map((c) => ({ ...c }))
        : DEFAULT_CHECKLIST.map((c) => ({ ...c })),
    );
    setScreenshots(enr?.screenshots ? [...enr.screenshots] : []);
    setTagIds((trade.tags ?? []).map((t) => t.id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade.id]);

  const toggleTag = (tid: number) =>
    setTagIds((prev) =>
      prev.includes(tid) ? prev.filter((x) => x !== tid) : [...prev, tid],
    );

  const addTag = () => {
    const name = newTag.trim();
    if (!name || createTag.isPending) return;
    createTag.mutate(
      { name },
      {
        onSuccess: (tag) => {
          setTagIds((prev) =>
            prev.includes(tag.id) ? prev : [...prev, tag.id],
          );
          setNewTag("");
        },
        onError: () =>
          toast({ title: "Couldn't create tag", variant: "destructive" }),
      },
    );
  };

  const addScreenshot = () => {
    const path = newShot.trim();
    if (!path) return;
    setScreenshots((prev) => (prev.includes(path) ? prev : [...prev, path]));
    setNewShot("");
  };

  const save = () => {
    const stopNum = stop.trim() === "" ? undefined : Number(stop);
    if (stopNum !== undefined && !Number.isFinite(stopNum)) {
      toast({ title: "Stop price must be a number", variant: "destructive" });
      return;
    }
    const body: PatchTradeRequest = {
      notes: notes.trim(),
      tag_ids: tagIds,
      checklist,
      screenshots,
      ...(grade ? { grade } : {}),
      ...(stopNum !== undefined ? { stop_price: stopNum } : {}),
    };
    patch.mutate(
      { id: trade.id, body },
      {
        onSuccess: (updated) => {
          const r = updated.r_multiple;
          toast({
            title: "Trade saved",
            description: r != null ? `R recomputed to ${formatR(r)}` : undefined,
            variant: "success",
          });
        },
        onError: (err) =>
          toast({
            title: "Save failed",
            description:
              err instanceof ApiError
                ? err.detail || err.message
                : "Could not save the trade.",
            variant: "destructive",
          }),
      },
    );
  };

  useImperativeHandle(ref, () => ({ save, isSaving: patch.isPending }));

  // Push in-flight state up so a host-rendered Save button (drawer footer) can
  // reflect it reactively — the imperative `isSaving` above is read-once and
  // won't re-render the host during a PATCH.
  useEffect(() => {
    onSavingChange?.(patch.isPending);
  }, [patch.isPending, onSavingChange]);

  return (
    <>
      <Section title="Tags">
        <div className="flex flex-wrap gap-1.5">
          {(allTags ?? []).length === 0 && tagIds.length === 0 ? (
            <span className="text-xs text-muted-foreground">
              No tags yet — create one below.
            </span>
          ) : (
            (allTags ?? []).map((t) => {
              const active = tagIds.includes(t.id);
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => toggleTag(t.id)}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-label font-medium transition-colors",
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t.name}
                </button>
              );
            })
          )}
        </div>
        <div className="flex gap-2">
          <Input
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addTag())}
            placeholder="New tag name…"
            className="h-8"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={addTag}
            disabled={!newTag.trim() || createTag.isPending}
          >
            <Plus className="h-3.5 w-3.5" /> Add
          </Button>
        </div>
      </Section>

      <Section title="Grade">
        <div className="flex gap-2">
          {GRADES.map((g) => (
            <button
              key={g}
              type="button"
              onClick={() => setGrade((cur) => (cur === g ? null : g))}
              className={cn(
                "h-9 w-9 rounded-md border text-sm font-semibold transition-colors",
                grade === g
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {g}
            </button>
          ))}
        </div>
      </Section>

      <Section title="Stop price">
        <div className="flex items-center gap-3">
          <Input
            type="number"
            value={stop}
            onChange={(e) => setStop(e.target.value)}
            placeholder="—"
            className="tabular h-8 w-32"
          />
          <span className="text-xs text-muted-foreground">
            Current R{" "}
            <span className={"tabular " + pnlColorClass(trade.r_multiple)}>
              {formatNumber(trade.r_multiple)}
            </span>{" "}
            · recomputed on save
          </span>
        </div>
      </Section>

      <Section title="Rule checklist">
        <ul className="space-y-1.5">
          {checklist.map((c, i) => (
            <li key={i}>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={c.checked}
                  onChange={(e) =>
                    setChecklist((prev) =>
                      prev.map((item, idx) =>
                        idx === i
                          ? { ...item, checked: e.target.checked }
                          : item,
                      ),
                    )
                  }
                  className="h-4 w-4 accent-primary"
                />
                <span
                  className={
                    c.checked ? "text-foreground" : "text-muted-foreground"
                  }
                >
                  {c.item}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Notes">
        <Textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="What was the setup? What did you learn?"
        />
      </Section>

      <Section title="Screenshots">
        {screenshots.length > 0 && (
          <ul className="space-y-1">
            {screenshots.map((path, i) => (
              <li
                key={`${path}-${i}`}
                className="flex items-center justify-between gap-2 rounded-md border border-border px-2 py-1"
              >
                <span className="tabular truncate text-xs">{path}</span>
                <button
                  type="button"
                  aria-label="Remove screenshot"
                  onClick={() =>
                    setScreenshots((prev) =>
                      prev.filter((_, idx) => idx !== i),
                    )
                  }
                  className="shrink-0 text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex gap-2">
          <Input
            value={newShot}
            onChange={(e) => setNewShot(e.target.value)}
            onKeyDown={(e) =>
              e.key === "Enter" && (e.preventDefault(), addScreenshot())
            }
            placeholder="/screenshots/2026-07-01-aapl.png"
            className="tabular h-8"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={addScreenshot}
            disabled={!newShot.trim()}
          >
            <Plus className="h-3.5 w-3.5" /> Add
          </Button>
        </div>
      </Section>

      {showSaveButton && (
        <div className="flex justify-end">
          <Button onClick={save} disabled={patch.isPending}>
            {patch.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      )}
    </>
  );
});

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <Label className="block">{title}</Label>
      {children}
    </section>
  );
}
