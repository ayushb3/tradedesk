import { useState } from "react";
import { AlertCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ApiError,
  netPnl,
  rMultiple,
  type Candle,
  type CreateSimTradeRequest,
  type SimTrade,
  type TradeDirection,
} from "@/lib/api";
import { formatEt } from "@/lib/chart";
import { DISPLAY_TZ_LABEL } from "@/lib/format";
import { useCreateSimTrade } from "@/lib/queries";

interface Fill {
  ts: string;
  price: number;
}

interface TradePanelProps {
  symbol: string;
  sessionDate: string;
  tz: string;
  /** Bar at the current playhead; null before data loads. */
  currentCandle: Candle | null;
  trades: SimTrade[];
  onTradeCreated: (t: SimTrade) => void;
}

/**
 * Log a simulated trade against the current replay bar. Entry/exit fills are
 * captured from the bar at the playhead ("Mark Entry"/"Mark Exit"), so a trade
 * can never reference a bar the user hasn't reached yet.
 */
export function TradePanel({
  symbol,
  sessionDate,
  tz,
  currentCandle,
  trades,
  onTradeCreated,
}: TradePanelProps) {
  const [direction, setDirection] = useState<TradeDirection>("long");
  const [size, setSize] = useState("100");
  const [entry, setEntry] = useState<Fill | null>(null);
  const [exit, setExit] = useState<Fill | null>(null);
  const [stop, setStop] = useState("");
  const [notes, setNotes] = useState("");

  const mutation = useCreateSimTrade();

  const qty = Number(size);
  const stopPrice = stop.trim() === "" ? undefined : Number(stop);
  const canSubmit =
    entry !== null &&
    exit !== null &&
    Number.isFinite(qty) &&
    qty > 0 &&
    (stopPrice === undefined || Number.isFinite(stopPrice)) &&
    !mutation.isPending;

  const markEntry = () => {
    if (currentCandle) setEntry({ ts: currentCandle.ts, price: currentCandle.close });
  };
  const markExit = () => {
    if (currentCandle) setExit({ ts: currentCandle.ts, price: currentCandle.close });
  };

  const submit = () => {
    if (!entry || !exit || !Number.isFinite(qty) || qty <= 0) return;
    const body: CreateSimTradeRequest = {
      symbol,
      direction,
      session_date: sessionDate,
      entries: [{ ts: entry.ts, qty, price: entry.price }],
      exits: [{ ts: exit.ts, qty, price: exit.price }],
      ...(stopPrice !== undefined ? { stop_price: stopPrice } : {}),
      ...(notes.trim() ? { notes: notes.trim() } : {}),
    };
    mutation.mutate(body, {
      onSuccess: (trade) => {
        onTradeCreated(trade);
        setEntry(null);
        setExit(null);
        setStop("");
        setNotes("");
      },
    });
  };

  const errorMessage =
    mutation.error instanceof ApiError
      ? mutation.error.detail || mutation.error.message
      : mutation.error instanceof Error
        ? mutation.error.message
        : mutation.error
          ? "Failed to log trade."
          : null;

  const disabledMark = !currentCandle;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto p-3">
      <section className="space-y-3">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Log sim trade
        </h2>

        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label htmlFor="direction">Direction</Label>
            <Select
              value={direction}
              onValueChange={(v) => setDirection(v as TradeDirection)}
            >
              <SelectTrigger id="direction">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="long">Long</SelectItem>
                <SelectItem value="short">Short</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="size">Size</Label>
            <Input
              id="size"
              type="number"
              min={0}
              value={size}
              onChange={(e) => setSize(e.target.value)}
              className="tabular"
            />
          </div>
        </div>

        <FillRow
          label="Entry"
          fill={entry}
          tz={tz}
          onMark={markEntry}
          onPriceChange={(price) =>
            setEntry((f) => (f ? { ...f, price } : f))
          }
          markDisabled={disabledMark}
        />
        <FillRow
          label="Exit"
          fill={exit}
          tz={tz}
          onMark={markExit}
          onPriceChange={(price) => setExit((f) => (f ? { ...f, price } : f))}
          markDisabled={disabledMark}
        />

        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label htmlFor="stop">Stop (optional)</Label>
            <Input
              id="stop"
              type="number"
              value={stop}
              onChange={(e) => setStop(e.target.value)}
              placeholder="—"
              className="tabular"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="notes">Notes (optional)</Label>
            <Input
              id="notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="setup, thesis…"
            />
          </div>
        </div>

        <Button
          className="w-full"
          disabled={!canSubmit}
          onClick={submit}
        >
          {mutation.isPending ? "Logging…" : "Log trade"}
        </Button>

        {errorMessage && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Couldn't log trade</AlertTitle>
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        )}
      </section>

      <section className="min-h-0 space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Session trades ({trades.length})
        </h2>
        {trades.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No trades logged yet. Mark an entry and exit, then log the trade.
          </p>
        ) : (
          <ul className="space-y-2">
            {trades.map((t, i) => (
              <TradeRow key={t.id ?? i} trade={t} index={i} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function FillRow({
  label,
  fill,
  tz,
  onMark,
  onPriceChange,
  markDisabled,
}: {
  label: string;
  fill: Fill | null;
  tz: string;
  onMark: () => void;
  onPriceChange: (price: number) => void;
  markDisabled: boolean;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label>{label}</Label>
        <span className="tabular text-label text-muted-foreground">
          {fill ? `${formatEt(fill.ts, tz)} ${DISPLAY_TZ_LABEL}` : "not set"}
        </span>
      </div>
      <div className="flex gap-2">
        <Input
          type="number"
          value={fill ? fill.price : ""}
          onChange={(e) => onPriceChange(Number(e.target.value))}
          disabled={!fill}
          placeholder="price"
          className="tabular"
        />
        <Button
          variant="outline"
          onClick={onMark}
          disabled={markDisabled}
          className="shrink-0"
        >
          Mark {label}
        </Button>
      </div>
    </div>
  );
}

function TradeRow({ trade, index }: { trade: SimTrade; index: number }) {
  const pnl = netPnl(trade);
  const r = rMultiple(trade);
  const entryPx = trade.entries[0]?.price;
  const exitPx = trade.exits[0]?.price;
  const qty = trade.entries[0]?.qty;
  const pnlColor =
    pnl == null
      ? "text-muted-foreground"
      : pnl >= 0
        ? "text-[#26a69a]"
        : "text-[#ef5350]";

  return (
    <li className="rounded-md border border-border bg-card px-3 py-2 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-medium">
          #{index + 1}{" "}
          <span
            className={
              trade.direction === "long" ? "text-[#26a69a]" : "text-[#ef5350]"
            }
          >
            {trade.direction.toUpperCase()}
          </span>{" "}
          <span className="text-muted-foreground tabular">
            {qty ?? "?"} @ {fmt(entryPx)} → {fmt(exitPx)}
          </span>
        </span>
      </div>
      <div className="mt-1 flex items-center gap-4">
        <span className={"tabular " + pnlColor}>
          P&L {pnl == null ? "—" : (pnl >= 0 ? "+" : "") + pnl.toFixed(2)}
        </span>
        <span className="tabular text-muted-foreground">
          R {r == null ? "—" : r.toFixed(2)}
        </span>
      </div>
      {trade.notes ? (
        <p className="mt-1 text-muted-foreground">{trade.notes}</p>
      ) : null}
    </li>
  );
}

function fmt(n: number | undefined): string {
  return n == null || !Number.isFinite(n) ? "—" : n.toFixed(2);
}
