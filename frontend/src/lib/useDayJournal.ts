/**
 * Debounced autosave for one free-text day journal field (why / how_felt / notes),
 * backed by GET/PUT /api/days/{date}/fields.
 *
 * Every save is a partial PUT of this field only, so the Day page's three fields and
 * the trade page's Day note never overwrite each other. A pending edit is flushed on
 * blur, when the date changes and on unmount, so leaving the page does not lose it.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api, type DayFields, type DayTextField } from "./api";
import { useDayFields } from "./queries";

export type SaveStatus = "idle" | "dirty" | "saving" | "saved" | "error";

export interface DayTextFieldState {
  value: string;
  onChange: (text: string) => void;
  /** Save now (blur). */
  flush: () => void;
  /** Re-send the current text after an error. */
  retry: () => void;
  status: SaveStatus;
  /** False until the saved value has loaded; the field stays read-only until then. */
  ready: boolean;
}

export function useDayTextField(date: string, field: DayTextField, delayMs = 800): DayTextFieldState {
  const qc = useQueryClient();
  const query = useDayFields(date || null);
  const server = query.data?.[field] ?? "";

  const [value, setValue] = useState(server);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const valueRef = useRef(value);
  const dateRef = useRef(date);
  const dirty = useRef(false);
  const pending = useRef<{ date: string; text: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seq = useRef(0);

  const save = useCallback(
    async (d: string, text: string) => {
      const my = ++seq.current;
      if (d === dateRef.current) setStatus("saving");
      try {
        const row = await api.putDayFields(d, { [field]: text });
        if (my !== seq.current) return; // a newer save owns the cache and the status
        qc.setQueryData<DayFields | null>(["day-fields", d], row);
        if (d === dateRef.current) {
          dirty.current = pending.current !== null;
          setStatus(pending.current ? "dirty" : "saved");
        }
      } catch {
        if (my === seq.current && d === dateRef.current) setStatus("error");
      }
    },
    [field, qc],
  );

  const flush = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const p = pending.current;
    pending.current = null;
    if (p) void save(p.date, p.text);
  }, [save]);

  // A new date: flush the old day's edit (cleanup), then start clean on the new one.
  useEffect(() => {
    dateRef.current = date;
    dirty.current = false;
    setStatus("idle");
    return () => flush();
  }, [date, flush]);

  // Take the saved value whenever there is no local edit in flight.
  useEffect(() => {
    if (!dirty.current) {
      valueRef.current = server;
      setValue(server);
    }
  }, [server, date]);

  const onChange = useCallback(
    (text: string) => {
      valueRef.current = text;
      setValue(text);
      dirty.current = true;
      pending.current = { date, text };
      setStatus("dirty");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(flush, delayMs);
    },
    [date, delayMs, flush],
  );

  const retry = useCallback(() => {
    pending.current = { date, text: valueRef.current };
    flush();
  }, [date, flush]);

  return { value, onChange, flush, retry, status, ready: !query.isLoading };
}
