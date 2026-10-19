import { Link } from "react-router-dom";

import { Skeleton } from "@/components/ui/skeleton";
import { useTrades } from "@/lib/queries";
import { DISPLAY_TZ_LABEL, formatClock, formatDayMonth, formatR } from "@/lib/format";

const MAX_ROWS = 40;
export const DEFAULT_LEAD = 15;

/** Landing for /replay with no trade: recent real trades, newest first. */
export function TradePicker({ onSymbolMode }: { onSymbolMode: () => void }) {
  const { data, isLoading, isError } = useTrades({ simulated: false });
  const rows = [...(data ?? [])]
    .sort((a, b) => Date.parse(b.open_ts) - Date.parse(a.open_ts))
    .slice(0, MAX_ROWS);

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-6">
      <h1 className="text-key">Replay a trade</h1>
      <p className="mt-1 text-body text-muted-foreground">
        Pick a recent trade. The replay starts {DEFAULT_LEAD} minutes before your entry.
      </p>

      <div className="mt-4 overflow-hidden rounded-md border border-border">
        {isLoading && (
          <div className="flex flex-col gap-2 p-3">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        )}
        {isError && <p className="p-4 text-body text-muted-foreground">Trades could not be loaded.</p>}
        {!isLoading && !isError && rows.length === 0 && (
          <p className="p-4 text-body text-muted-foreground">No trades yet.</p>
        )}
        <ul className="divide-y divide-border">
          {rows.map((t) => (
            <li key={t.id}>
              <Link
                to={`/replay?trade=${t.id}&lead=${DEFAULT_LEAD}`}
                className="grid grid-cols-[6rem_6rem_1fr_5rem] items-baseline gap-3 px-4 py-3 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
              >
                <span className="num text-value">{formatDayMonth(t.session_date)}</span>
                <span className="num text-value text-muted-foreground">
                  {formatClock(t.open_ts)} {DISPLAY_TZ_LABEL}
                </span>
                <span className="num truncate text-value">{t.contract_label ?? t.symbol}</span>
                <span className="num text-right text-value">{formatR(t.r_multiple)}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>

      <button
        type="button"
        onClick={onSymbolMode}
        className="mt-4 text-label text-muted-foreground underline-offset-4 hover:underline"
      >
        Replay a symbol instead
      </button>
    </div>
  );
}
