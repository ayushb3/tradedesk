/**
 * Replay as an action on a trade or a day: where "Exit replay" goes, and how to link in.
 * Pure helpers (checked in replayNav.check.ts).
 */

/** `from=` is only honoured for an in-app path ("/days/2026-11-04"), never another origin. */
export function safeFrom(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const v = raw.trim();
  if (!v.startsWith("/") || v.startsWith("//") || v.startsWith("/\\")) return null;
  if (/[\u0000-\u001f]/.test(v)) return null;
  return v;
}

/** Exit: the `from` path; else the trade's page; else null (the caller goes to the Days calendar). */
export function exitTarget(from: string | null, tradeId: number | null): string | null {
  if (from) return from;
  if (tradeId !== null) return `/trades/${tradeId}`;
  return null;
}

export const isDay = (v: string | null | undefined): v is string =>
  typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);

/** Link to replay one trade, coming back to `from` on Exit. */
export function tradeReplayHref(tradeId: number, from?: string | null, lead = 15): string {
  const qs = new URLSearchParams({ trade: String(tradeId), lead: String(lead) });
  const f = safeFrom(from ?? null);
  if (f) qs.set("from", f);
  return `/replay?${qs.toString()}`;
}

const etHm = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const etMinuteOfDay = (sec: number): number => {
  const parts = etHm.formatToParts(sec * 1000);
  return Number(parts.find((p) => p.type === "hour")?.value) * 60 + Number(parts.find((p) => p.type === "minute")?.value);
};

/**
 * A day replay's window over a session's 1-minute bars: `leadMin` before the 09:30 ET open
 * to the last bar before 16:00 ET (06:30 to 13:00 PT). Falls back to the whole session when
 * the bars carry no regular hours. Null with no bars.
 */
export function rthWindow(barSecs: number[], leadMin = 15): { startSec: number; endSec: number } | null {
  if (barSecs.length === 0) return null;
  const open = 9 * 60 + 30 - leadMin;
  const close = 16 * 60;
  const start = barSecs.find((s) => etMinuteOfDay(s) >= open);
  const inHours = barSecs.filter((s) => etMinuteOfDay(s) < close);
  const end = inHours.length ? inHours[inHours.length - 1] : undefined;
  if (start === undefined || end === undefined || end < start) {
    return { startSec: barSecs[0], endSec: barSecs[barSecs.length - 1] };
  }
  return { startSec: start, endSec: end };
}
