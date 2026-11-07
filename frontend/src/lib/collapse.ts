/**
 * Per-viewer open/closed state for collapsible sections, kept in localStorage. Every read
 * and write is wrapped: private windows and blocked storage fall back to the default.
 */
import { useCallback, useState } from "react";

const PREFIX = "td.section.";

export function readOpen(id: string, fallback: boolean): boolean {
  try {
    const v = window.localStorage.getItem(PREFIX + id);
    return v === "1" ? true : v === "0" ? false : fallback;
  } catch {
    return fallback;
  }
}

export function writeOpen(id: string, open: boolean): void {
  try {
    window.localStorage.setItem(PREFIX + id, open ? "1" : "0");
  } catch {
    /* storage unavailable: the choice lasts for this page view only */
  }
}

export function useSectionOpen(id: string, defaultOpen: boolean): [boolean, (open: boolean) => void] {
  const [open, setOpenState] = useState(() => readOpen(id, defaultOpen));
  const setOpen = useCallback(
    (v: boolean) => {
      setOpenState(v);
      writeOpen(id, v);
    },
    [id],
  );
  return [open, setOpen];
}
