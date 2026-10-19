/**
 * Pure helpers for the Trade review chart and rail (static view of the stacked
 * contract-over-SPY chart). Trade review is post-trade, so the exit is visible.
 */
import type { CandlestickData, UTCTimestamp, WhitespaceData } from "lightweight-charts";

import type { Trade, TradeExcursions } from "./api";
const isoToUtcSeconds = (iso: string): number => Math.floor(new Date(iso).getTime() / 1000);
import { DISPLAY_TZ_LABEL, formatClock, formatCurrency } from "./format";
const MINUTE = 60;
const floorMinute = (sec: number): number => Math.floor(sec / MINUTE) * MINUTE;

export const LEAD_MIN = 15;
export const TAIL_MIN = 30;
/** The after-exit bars are drawn in this grey. */
export const AFTER_EXIT_GREY = "#71717a";

export interface ReviewWindow {
  startSec: number;
  endSec: number;
  /** Minute of the (last) exit, or null for an open trade. */
  exitSec: number | null;
}

/**
 * x-range: 15 min before entry to 30 min after exit, clamped to the session's bars.
 * An open trade runs to the last bar.
 */
export function reviewWindow(
  openTs: string,
  closeTs: string | null,
  firstBarSec: number,
  lastBarSec: number,
): ReviewWindow {
  const entry = floorMinute(isoToUtcSeconds(openTs));
  const exit = closeTs ? floorMinute(isoToUtcSeconds(closeTs)) : null;
  const start = Math.max(floorMinute(firstBarSec), entry - LEAD_MIN * MINUTE);
  const rawEnd = exit === null ? lastBarSec : exit + TAIL_MIN * MINUTE;
  const end = Math.min(lastBarSec, rawEnd);
  if (!(start < end)) return { startSec: floorMinute(firstBarSec), endSec: lastBarSec, exitSec: exit };
  return { startSec: start, endSec: end, exitSec: exit };
}

/** Last minute whose bar is drawn: the window end, or the exit while after-exit bars are hidden. */
export function revealBound(endSec: number, exitSec: number | null, showAfterExit: boolean): number {
  return showAfterExit || exitSec === null ? endSec : Math.min(endSec, exitSec);
}

type Bar = CandlestickData<UTCTimestamp> | WhitespaceData<UTCTimestamp>;

/** Bars after the exit minute get a flat grey (body, border and wick). */
export function greyAfterExit(data: Bar[], exitSec: number | null): Bar[] {
  if (exitSec === null) return data;
  return data.map((b) =>
    (b.time as number) > exitSec && "open" in b
      ? { ...b, color: AFTER_EXIT_GREY, borderColor: AFTER_EXIT_GREY, wickColor: AFTER_EXIT_GREY }
      : b,
  );
}

export interface LogicalRange {
  from: number;
  to: number;
}

/**
 * Zoom a logical (bar index) range around `anchor`. factor < 1 zooms in. The result
 * keeps at least `minBars` bars and never exceeds the full range [0, last].
 */
export function zoomRange(r: LogicalRange, anchor: number, factor: number, last: number, minBars = 8): LogicalRange {
  const full = last + 1;
  const width = Math.min(full, Math.max(Math.min(minBars, full), (r.to - r.from) * factor));
  const frac = r.to === r.from ? 0.5 : Math.min(1, Math.max(0, (anchor - r.from) / (r.to - r.from)));
  let from = anchor - width * frac;
  let to = from + width;
  if (from < 0) {
    to -= from;
    from = 0;
  }
  if (to > last) {
    from -= to - last;
    to = last;
  }
  return { from: Math.max(0, from), to };
}

/** Wheel zooms the chart only while Ctrl, Cmd or Alt is held; plain wheel scrolls the page. */
export function isZoomWheel(e: { ctrlKey: boolean; metaKey: boolean; altKey: boolean }): boolean {
  return e.ctrlKey || e.metaKey || e.altKey;
}

// ---------------------------------------------------------------------------
// Rail: written before entry

export interface WrittenRow {
  key: "max_loss" | "stop" | "size";
  label: string;
  value: string;
  /** True when the number is an assumption rather than something written before entry. */
  assumed: boolean;
}

/** Max loss, stop premium and size, as written before entry. Assumed values say so. */
export function writtenBeforeEntry(trade: Trade, exc: TradeExcursions | null | undefined): WrittenRow[] {
  const written = exc ? exc.risk_source === "written_max_loss" : trade.r_basis === "written";
  const risk = exc?.risk ?? null;
  const stop = exc?.stop_price ?? trade.stop_price ?? null;
  return [
    {
      key: "max_loss",
      label: "Max loss",
      value: written && risk !== null ? formatCurrency(risk) : "not written",
      assumed: !written,
    },
    {
      key: "stop",
      label: "Stop premium",
      value: stop !== null ? stop.toFixed(2) : "none",
      assumed: !written && stop !== null,
    },
    { key: "size", label: "Size", value: `${trade.qty} ${trade.qty === 1 ? "contract" : "contracts"}`, assumed: false },
  ];
}

// ---------------------------------------------------------------------------
// Debrief timestamps

/** The API sends UTC; a bare ISO string (no zone) is read as UTC, never as the browser's zone. */
export function ensureUtc(iso: string): string {
  return /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`;
}

/** "Started 07:12 PT" for a debrief's created_at. */
export function debriefStarted(createdAt: string | null | undefined): string | null {
  if (!createdAt) return null;
  const clock = formatClock(ensureUtc(createdAt));
  return clock === "—" ? null : `Started ${clock} ${DISPLAY_TZ_LABEL}`;
}
