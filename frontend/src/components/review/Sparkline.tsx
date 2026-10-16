import { useMemo } from "react";

import type { Sparkline as SparklineData } from "@/lib/api";
import { cn } from "@/lib/utils";

interface SparklineProps {
  /** Downsampled underlying close prices over the hold window. */
  data: SparklineData;
  width?: number;
  height?: number;
  className?: string;
}

/**
 * A tiny axis-less line glyph drawn from a Sparkline's `values`. Purely a scan
 * aid on day cards — no labels, gridlines, or P&L coloring (anti-gambling:
 * neutral by design; the shape communicates path, not outcome). Uses a single
 * muted stroke so a green/red bias never reads as celebration.
 */
export function Sparkline({
  data,
  width = 96,
  height = 28,
  className,
}: SparklineProps) {
  const path = useMemo(() => {
    const values = data.values;
    if (!values || values.length === 0) return null;
    if (values.length === 1) {
      const y = height / 2;
      return `M 0 ${y} L ${width} ${y}`;
    }
    let min = Infinity;
    let max = -Infinity;
    for (const v of values) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const span = max - min || 1; // flat series → a centered line
    const pad = 1.5; // keep the stroke off the top/bottom edge
    const usable = height - pad * 2;
    const stepX = width / (values.length - 1);
    return values
      .map((v, i) => {
        const x = i * stepX;
        const y = pad + (1 - (v - min) / span) * usable;
        return `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ");
  }, [data.values, width, height]);

  if (!path) return null;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("overflow-visible", className)}
      role="img"
      aria-label="Underlying price path over the hold window"
      preserveAspectRatio="none"
    >
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.25}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-muted-foreground"
      />
    </svg>
  );
}
