import type { ReactNode } from "react";
import { AlertCircle, Check, Loader2 } from "lucide-react";

import { Textarea } from "@/components/ui/textarea";
import type { DayTextField } from "@/lib/api";
import { useDayTextField, type SaveStatus } from "@/lib/useDayJournal";
import { cn } from "@/lib/utils";

/** "Saving…" / "Saved" / "Couldn't save · Retry", small and to the right of a field label. */
export function SaveIndicator({ status, onRetry }: { status: SaveStatus; onRetry?: () => void }) {
  if (status === "saving") {
    return (
      <span className="inline-flex items-center gap-1 text-label text-muted-foreground" aria-live="polite">
        <Loader2 className="h-3 w-3 animate-spin" />
        Saving…
      </span>
    );
  }
  if (status === "saved") {
    return (
      <span className="inline-flex items-center gap-1 text-label text-muted-foreground" aria-live="polite">
        <Check className="h-3 w-3 text-gain" />
        Saved
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="inline-flex items-center gap-1 text-label text-loss" aria-live="polite">
        <AlertCircle className="h-3 w-3" />
        Couldn't save
        {onRetry && (
          <button type="button" onClick={onRetry} className="font-medium underline-offset-2 hover:underline">
            Retry
          </button>
        )}
      </span>
    );
  }
  return null;
}

/** One day journal text field with autosave. */
export function DayJournalField({
  date,
  field,
  label,
  placeholder,
  hint,
  className,
  rows = 3,
}: {
  date: string;
  field: DayTextField;
  label: string;
  placeholder?: string;
  hint?: ReactNode;
  className?: string;
  rows?: number;
}) {
  const f = useDayTextField(date, field);
  const id = `day-${field}`;
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-label label-caps text-muted-foreground">
          {label}
        </label>
        <SaveIndicator status={f.status} onRetry={f.retry} />
      </div>
      {hint}
      <Textarea
        id={id}
        rows={rows}
        value={f.value}
        readOnly={!f.ready}
        onChange={(e) => f.onChange(e.target.value)}
        onBlur={f.flush}
        placeholder={f.ready ? placeholder : "Loading…"}
        className="min-h-[64px] text-body"
      />
    </div>
  );
}

/**
 * The day journal on the Day page: why, how it felt, and notes. Saved to the day record
 * (the same one the MCP `day_fields` tool writes). The review's reflection prompts are
 * shown as questions to answer in the notes.
 */
export function DayJournal({ date, prompts }: { date: string; prompts: string[] }) {
  return (
    <section className="space-y-3 rounded-lg border border-border bg-card p-4" data-testid="day-journal">
      <h2 className="text-sm font-semibold tracking-tight">Day journal</h2>
      <div className="grid gap-3 md:grid-cols-2">
        <DayJournalField
          date={date}
          field="why"
          label="Why I traded"
          placeholder="The setup or call that put you in today, or why you sat out."
          rows={2}
        />
        <DayJournalField
          date={date}
          field="how_felt"
          label="How it felt"
          placeholder="Calm, rushed, frustrated… and when it changed."
          rows={2}
        />
      </div>
      <DayJournalField
        date={date}
        field="notes"
        label="Day note"
        placeholder="What happened, and the one adjustment for tomorrow."
        rows={5}
        hint={
          prompts.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-5 text-label text-muted-foreground">
              {prompts.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          ) : undefined
        }
      />
    </section>
  );
}
