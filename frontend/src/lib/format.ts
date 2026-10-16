/**
 * Display formatters for the journal / analytics UI. All tolerate
 * null/undefined so components can render partial or missing backend data
 * without guarding at every call site.
 */

/**
 * Zone every clock time in the UI is shown in. Ayush trades from the US West
 * Coast and his rules are written in Pacific time, so the UI shows PT. Storage
 * stays UTC and the trading session/day logic stays US Eastern (exchange time);
 * only the displayed wall clock changes.
 */
export const DISPLAY_TZ = "America/Los_Angeles";
export const DISPLAY_TZ_LABEL = "PT";

/** TradingView-ish green/red used across the app for P&L and direction. */
export const PNL_GREEN = "#26a69a";
export const PNL_RED = "#ef5350";

/** Tailwind text-color class for a signed number (null → muted). */
export function pnlColorClass(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "text-muted-foreground";
  return n >= 0 ? "text-[#26a69a]" : "text-[#ef5350]";
}

function isNum(n: number | null | undefined): n is number {
  return n != null && Number.isFinite(n);
}

/** Signed currency, e.g. `+$1,240.50` / `-$318.00`. */
export function formatCurrency(
  n: number | null | undefined,
  opts: { sign?: boolean } = {},
): string {
  if (!isNum(n)) return "—";
  const sign = opts.sign && n > 0 ? "+" : n < 0 ? "-" : "";
  const abs = Math.abs(n).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${sign}$${abs}`;
}

/** Signed P&L, always prefixed with + / -. */
export function formatPnl(n: number | null | undefined): string {
  return formatCurrency(n, { sign: true });
}

/** A ratio 0..1 (or already a percent) rendered as `63.2%`. */
export function formatPercent(
  n: number | null | undefined,
  opts: { fromRatio?: boolean; digits?: number } = {},
): string {
  if (!isNum(n)) return "—";
  const pct = opts.fromRatio === false ? n : n <= 1 && n >= -1 ? n * 100 : n;
  return `${pct.toFixed(opts.digits ?? 1)}%`;
}

/** Signed R multiple, e.g. `+1.8R`. */
export function formatR(n: number | null | undefined): string {
  if (!isNum(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}R`;
}

/** Plain fixed number, `—` when missing. */
export function formatNumber(
  n: number | null | undefined,
  digits = 2,
): string {
  return isNum(n) ? n.toFixed(digits) : "—";
}

/** Profit factor: unbounded, `∞` when there are no losses. */
export function formatFactor(n: number | null | undefined): string {
  if (!isNum(n)) return "—";
  if (!Number.isFinite(n)) return "∞";
  return n.toFixed(2);
}

/** Seconds → compact hold time, e.g. `1h 23m`, `45m`, `38s`. */
export function formatHoldTime(seconds: number | null | undefined): string {
  if (!isNum(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

/**
 * Today's calendar date in US Eastern (the trading day), as `YYYY-MM-DD`.
 * Uses the ET timezone rather than the browser's local clock so a user
 * outside US Eastern still defaults to the correct trading session.
 * `en-CA` locale renders dates in ISO `YYYY-MM-DD` order.
 */
export function todayET(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

/** ISO/`YYYY-MM-DD` → `Jul 1`. */
export function formatDayMonth(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/**
 * ISO timestamp → `HH:MM` in the display zone (`DISPLAY_TZ`, Pacific).
 *
 * Timestamps come off the backend as UTC ending in `Z`. The charts' time axes
 * use the same zone, so the header, day cards and fills table agree with the
 * chart (DST handled by the zone, not a fixed offset). Callers label the
 * result with `DISPLAY_TZ_LABEL`.
 */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: DISPLAY_TZ,
  });
}
