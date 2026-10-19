import { useState } from "react";
import { format, parse, isValid } from "date-fns";
import { CalendarIcon, ChevronsUpDown, Check } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

/** Curated suggestion list — used as typeahead options. */
export const SYMBOLS = ["AAPL", "RIVN", "HOOD", "TSLA", "NVDA", "META", "AMZN", "SPY", "QQQ"] as const;

/** Widened to string so ReplayPage can freely set any ticker. */
export type Symbol = string;

interface ReplayToolbarProps {
  symbol: Symbol;
  onSymbolChange: (s: Symbol) => void;
  date: string;
  onDateChange: (d: string) => void;
  /** Replay transport controls, rendered at the right of the bar. */
  controls?: ReactNode;
}

/** Top control bar: symbol combobox + calendar date picker + replay controls slot. */
export function ReplayToolbar({
  symbol,
  onSymbolChange,
  date,
  onDateChange,
  controls,
}: ReplayToolbarProps) {
  return (
    <div className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4">
      <div className="flex items-center gap-2">
        <Label htmlFor="symbol">Symbol</Label>
        <SymbolCombobox
          value={symbol}
          onValueChange={onSymbolChange}
        />
      </div>

      <div className="flex items-center gap-2">
        <Label htmlFor="date">Date</Label>
        <DatePickerButton
          value={date}
          onValueChange={onDateChange}
        />
      </div>

      <div className="ml-auto">{controls}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SymbolCombobox — free-text typeahead with curated SYMBOLS suggestions.
// ---------------------------------------------------------------------------

function SymbolCombobox({
  value,
  onValueChange,
}: {
  value: string;
  onValueChange: (s: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");

  /** Normalise: uppercase, trim, only A-Z */
  function normalise(raw: string): string {
    return raw.trim().toUpperCase().replace(/[^A-Z]/g, "");
  }

  function commit(ticker: string) {
    const norm = normalise(ticker);
    if (!norm) return;
    onValueChange(norm);
    setInputValue("");
    setOpen(false);
  }

  const filtered = SYMBOLS.filter((s) =>
    inputValue ? s.includes(normalise(inputValue)) : true,
  );

  // Whether the typed input is already in the list (to avoid duplicate entry row).
  const normInput = normalise(inputValue);
  const showCustom = normInput.length > 0 && !SYMBOLS.includes(normInput as typeof SYMBOLS[number]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id="symbol"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-haspopup="listbox"
          className="w-28 justify-between font-mono tabular"
        >
          {value}
          <ChevronsUpDown className="ml-1 h-3.5 w-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-48 p-0" align="start" sideOffset={4}>
        <Command>
          <CommandInput
            placeholder="Search ticker…"
            value={inputValue}
            onValueChange={setInputValue}
            onKeyDown={(e) => {
              if (e.key === "Enter" && normInput.length > 0) {
                commit(normInput);
              }
            }}
          />
          <CommandList>
            <CommandEmpty>
              {normInput.length > 0 ? (
                <button
                  type="button"
                  className="w-full px-2 py-1.5 text-left text-body hover:bg-accent"
                  onClick={() => commit(normInput)}
                >
                  Use &ldquo;{normInput}&rdquo;
                </button>
              ) : (
                "No symbols found."
              )}
            </CommandEmpty>
            <CommandGroup>
              {filtered.map((s) => (
                <CommandItem
                  key={s}
                  value={s}
                  onSelect={() => commit(s)}
                  className="font-mono"
                >
                  <Check
                    className={cn(
                      "mr-2 h-4 w-4",
                      value === s ? "opacity-100" : "opacity-0",
                    )}
                  />
                  {s}
                </CommandItem>
              ))}
              {/* Arbitrary ticker entry when not in suggestions */}
              {showCustom && (
                <CommandItem
                  value={normInput}
                  onSelect={() => commit(normInput)}
                  className="font-mono italic"
                >
                  <Check className="mr-2 h-4 w-4 opacity-0" />
                  {normInput}
                </CommandItem>
              )}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

// ---------------------------------------------------------------------------
// DatePickerButton — calendar popover, value kept as YYYY-MM-DD string.
// ---------------------------------------------------------------------------

const DATE_FMT = "yyyy-MM-dd";
const DISPLAY_FMT = "MMM d, yyyy";

function DatePickerButton({
  value,
  onValueChange,
}: {
  value: string;
  onValueChange: (d: string) => void;
}) {
  const [open, setOpen] = useState(false);

  // Parse the YYYY-MM-DD string to a Date (date-fns parse uses noon-local to
  // avoid DST boundary issues when formatting back).
  const parsed = parse(value, DATE_FMT, new Date());
  const selected = isValid(parsed) ? parsed : undefined;

  function handleSelect(day: Date | undefined) {
    if (!day) return;
    onValueChange(format(day, DATE_FMT));
    setOpen(false);
  }

  const displayLabel = selected ? format(selected, DISPLAY_FMT) : value;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id="date"
          variant="outline"
          className={cn(
            "w-40 justify-start font-normal tabular",
            !selected && "text-muted-foreground",
          )}
          aria-label={`Selected date: ${displayLabel}. Click to open calendar.`}
        >
          <CalendarIcon className="mr-2 h-4 w-4" />
          {displayLabel}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start" sideOffset={4}>
        <Calendar
          mode="single"
          selected={selected}
          onSelect={handleSelect}
          disabled={(day) => day > new Date()}
          initialFocus
        />
      </PopoverContent>
    </Popover>
  );
}
