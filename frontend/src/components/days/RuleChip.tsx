import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/dayPage";

const TONE: Record<Tone, string> = {
  kept: "border-gain/40 bg-gain/10 text-gain",
  broken: "border-loss/40 bg-loss/10 text-loss",
  unknown: "border-border bg-muted text-muted-foreground",
};

/**
 * A small pill for one rule state: green kept, red broken, grey unknown.
 * Deliberately generic (label + tone + optional tooltip) so it can be unified
 * with other rule chips later.
 */
export function RuleChip({
  label,
  tone,
  title,
  className,
}: {
  label: string;
  tone: Tone;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-label font-medium",
        TONE[tone],
        className,
      )}
    >
      {label}
    </span>
  );
}
