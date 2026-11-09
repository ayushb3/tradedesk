/**
 * Free replay's contract picker, Legend style: Calls | Puts, a strike stepper around the
 * price, and the expiry fixed to the replay day (0DTE). Locked while a practice position
 * is open, so a trade can't flip contracts mid-way.
 */
import { Minus, Plus } from "lucide-react";

import { strikeStep, type OptionRoot } from "@/lib/freeOptions";
import { cn } from "@/lib/utils";

export type OptionSide = "call" | "put";

export function ContractPicker({
  root,
  side,
  onSideChange,
  strike,
  onStrikeChange,
  atm,
  date,
  locked,
}: {
  root: OptionRoot;
  side: OptionSide;
  onSideChange: (s: OptionSide) => void;
  strike: number;
  onStrikeChange: (k: number) => void;
  /** The at-the-money strike at the playhead; null before any bar. */
  atm: number | null;
  /** The replay day, YYYY-MM-DD: the contract's expiry. */
  date: string;
  /** True while a position is open. */
  locked: boolean;
}) {
  const step = strikeStep(root);
  const lockTitle = locked ? "Close the position to switch contracts" : undefined;
  const offset = atm != null ? Math.round((strike - atm) / step) : null;
  const sideBtn = (s: OptionSide, label: string) => (
    <button
      type="button"
      aria-pressed={side === s}
      disabled={locked}
      onClick={() => onSideChange(s)}
      className={cn(
        "px-2 py-0.5 text-label disabled:opacity-50",
        side === s
          ? s === "call"
            ? "bg-up/15 text-up"
            : "bg-down/15 text-down"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
  return (
    <div
      className="flex items-center gap-2"
      role="group"
      aria-label="Option contract"
      title={lockTitle}
      data-testid="contract-picker"
    >
      <div className="flex overflow-hidden rounded border border-border">
        {sideBtn("call", "Calls")}
        {sideBtn("put", "Puts")}
      </div>
      <div className="flex items-center rounded border border-border">
        <button
          type="button"
          aria-label="Lower strike"
          disabled={locked}
          onClick={() => onStrikeChange(strike - step)}
          className="px-1 py-0.5 text-muted-foreground hover:text-foreground disabled:opacity-50"
        >
          <Minus className="h-3 w-3" />
        </button>
        <span className="min-w-14 px-1 text-center font-mono text-label tabular" aria-label="Strike">
          {strike}
          {side === "call" ? "C" : "P"}
        </span>
        <button
          type="button"
          aria-label="Higher strike"
          disabled={locked}
          onClick={() => onStrikeChange(strike + step)}
          className="px-1 py-0.5 text-muted-foreground hover:text-foreground disabled:opacity-50"
        >
          <Plus className="h-3 w-3" />
        </button>
      </div>
      <button
        type="button"
        disabled={locked || atm == null || offset === 0}
        onClick={() => atm != null && onStrikeChange(atm)}
        className="rounded border border-border px-1.5 py-0.5 text-label text-muted-foreground hover:text-foreground disabled:opacity-50"
        title="Jump to the at-the-money strike"
      >
        {offset == null || offset === 0 ? "ATM" : `ATM ${offset > 0 ? "+" : ""}${offset}`}
      </button>
      <span className="text-label text-muted-foreground tabular">
        0DTE {date.slice(5, 7)}/{date.slice(8, 10)}
      </span>
      {locked && <span className="text-label text-muted-foreground">· locked</span>}
    </div>
  );
}
