/**
 * Overlay pieces for drawing the trade on the chart: a position pill sitting on
 * the average-cost line, draggable stop/target pills on their plan lines, and
 * fill tags (real or practice) that show their details on hover. All are
 * presentational and take pixel coordinates; they are absolutely positioned
 * inside an overlay div supplied by the chart, which is `pointer-events-none`,
 * so interactive pieces opt back in with `pointer-events-auto`.
 */
import { useState } from "react";
import { GripHorizontal, X } from "lucide-react";
import { CHART_THEME } from "@/lib/chartTheme";
import { cn } from "@/lib/utils";

/** A tint over the chart background, so the line under a pill never shows through its text. */
const solidTint = (color: string): React.CSSProperties => ({
  backgroundColor: "hsl(var(--chart-bg))",
  backgroundImage: `linear-gradient(${color}, ${color})`,
});
const UP_TINT = "hsl(var(--chart-up) / 0.15)";
const DOWN_TINT = "hsl(var(--chart-down) / 0.15)";

export interface PositionPillProps {
  /** "+10", "-2". */
  qtyText: string;
  /** Open % at the playhead; null when there is no mark. Colors the pill (green up, orange down). */
  pct: number | null;
  /** "▲17.08%". */
  pctText: string;
  /** "+$12.50": shown in place of the % while hovered. */
  pnlText: string;
  /** Hover title: the size and average. */
  title: string;
  /** An option position on its underlying's chart: "11/27 Call". */
  tag?: string;
  /** The ×: hide this pill on this chart. */
  onHide?: () => void;
}

/**
 * Legend's compact position pill on the average-cost line, hugging the price scale:
 * `+10 ▲17.08% ×`. Hover swaps the % for the dollar P&L.
 */
export function PositionPill({ qtyText, pct, pctText, pnlText, title, tag, onHide }: PositionPillProps) {
  const [hover, setHover] = useState(false);
  const up = pct !== null && pct > 0;
  const down = pct !== null && pct < 0;
  const tone = up ? "border-up/70 text-up" : down ? "border-down/70 text-down" : "border-border text-muted-foreground";
  const tint = up ? solidTint(UP_TINT) : down ? solidTint(DOWN_TINT) : { backgroundColor: "hsl(var(--chart-bg))" };
  return (
    <div
      data-testid="position-pill"
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className={cn(
        "num pointer-events-auto flex h-4 items-center gap-1 whitespace-nowrap rounded-sm border pl-1 text-[10px] leading-none",
        !onHide && "pr-1",
        tone,
      )}
      style={tint}
    >
      <span className="font-bold text-foreground">{qtyText}</span>
      {tag && <span className="text-foreground">{tag}</span>}
      <span data-testid="position-pill-value">{hover ? pnlText : pctText}</span>
      {onHide && (
        <button
          type="button"
          aria-label="Hide this position pill"
          title="Hide on this chart"
          className="flex h-full items-center px-0.5 text-muted-foreground hover:text-foreground"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={onHide}
        >
          <X className="h-2.5 w-2.5" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

export interface LinePillProps {
  y: number;
  kind: "stop" | "target";
  label: string;
  priceText: string;
  draggable?: boolean;
  onPointerDown?: React.PointerEventHandler<HTMLDivElement>;
  onPointerMove?: React.PointerEventHandler<HTMLDivElement>;
  onPointerUp?: React.PointerEventHandler<HTMLDivElement>;
  onPointerCancel?: React.PointerEventHandler<HTMLDivElement>;
  onClear?: () => void;
  right?: number;
}

export function LinePill({
  y,
  kind,
  label,
  priceText,
  draggable,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
  onClear,
  right = 8,
}: LinePillProps) {
  const isStop = kind === "stop";
  const style: React.CSSProperties = { top: y, right };
  if (isStop) {
    style.color = CHART_THEME.stop;
    style.borderColor = CHART_THEME.stop;
    Object.assign(style, solidTint(`${CHART_THEME.stop}26`)); // ~15% alpha over the chart bg
  } else {
    Object.assign(style, solidTint(UP_TINT));
  }
  const dragProps = draggable
    ? {
        role: "slider" as const,
        "aria-label": `${label} price`,
        "aria-valuetext": priceText,
        title: "Drag to move",
        onPointerDown,
        onPointerMove,
        onPointerUp,
        onPointerCancel,
      }
    : {};
  return (
    <div
      data-testid={`line-pill-${kind}`}
      {...dragProps}
      className={cn(
        "absolute flex -translate-y-1/2 items-center gap-1 rounded border px-1.5 text-label",
        !isStop && "border-up/60 text-up",
        draggable ? "pointer-events-auto cursor-row-resize touch-none" : "pointer-events-none",
      )}
      style={style}
    >
      {draggable && <GripHorizontal className="h-3 w-3" aria-hidden="true" />}
      <span className="font-semibold uppercase tracking-wide">{label}</span>
      <span className="num">{priceText}</span>
      {onClear && (
        <button
          type="button"
          aria-label={`Clear ${label}`}
          className="pointer-events-auto rounded hover:bg-foreground/10"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={onClear}
        >
          <X className="h-3 w-3" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

export interface FillTagProps {
  x: number;
  y: number;
  side: "buy" | "sell";
  text: string;
  details: string[];
  placeAbove: boolean;
  sim?: boolean;
}

export function FillTag({ x, y, side, text, details, placeAbove, sim }: FillTagProps) {
  const [open, setOpen] = useState(false);
  const tone = sim
    ? ""
    : side === "buy"
      ? "border-up/60 text-up"
      : "border-down/60 text-down";
  const simStyle: React.CSSProperties | undefined = sim
    ? { color: "#3b82f6", borderColor: "#3b82f6", ...solidTint("#3b82f626") }
    : undefined;
  const placement: React.CSSProperties = placeAbove
    ? { left: x, bottom: `calc(100% - ${y - 4}px)` }
    : { left: x, top: y + 4 };
  return (
    <button
      type="button"
      data-testid={`fill-tag-${side}`}
      aria-label={details.join(", ")}
      tabIndex={0}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      className={cn(
        "num pointer-events-auto absolute -translate-x-1/2 whitespace-nowrap rounded-sm border bg-chart px-[3px] text-[9px] leading-[12px]",
        tone,
      )}
      style={{ ...placement, ...simStyle }}
    >
      {text}
      {open && details.length > 0 && (
        <span
          role="tooltip"
          className={cn(
            "absolute left-1/2 z-20 -translate-x-1/2 whitespace-nowrap rounded border border-border bg-card px-2 py-1 text-left text-label text-foreground shadow",
            placeAbove ? "bottom-full mb-1" : "top-full mt-1",
          )}
        >
          {details.map((line) => (
            <span key={line} className="block">
              {line}
            </span>
          ))}
        </span>
      )}
    </button>
  );
}
