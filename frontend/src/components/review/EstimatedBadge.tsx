import { Info } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * The tooltip copy is deliberately explicit: the premium series shown is a
 * Black-Scholes reconstruction, pinned to the trader's real executions, and is
 * a review aid — never an analytics input (PRD-M7 §7, R5).
 */
const TOOLTIP =
  "Estimated premium — this contract's price series is reconstructed with a " +
  "Black-Scholes model and anchored to your actual fill prices. It's a review " +
  "aid to see how the position moved, not an analytics input.";

/**
 * Visible marker that the option premium curve is synthetic (BS fallback),
 * shown whenever `option.method === "bs_interp"` / `option.synthetic`. Uses a
 * native `title` tooltip so it needs no extra dependency and stays accessible.
 */
export function EstimatedBadge({ className }: { className?: string }) {
  return (
    <Badge
      variant="outline"
      title={TOOLTIP}
      className={cn(
        "cursor-help border-[#d9a441]/40 text-[#d9a441]",
        className,
      )}
    >
      <Info className="h-3 w-3" aria-hidden />
      estimated premium
    </Badge>
  );
}
