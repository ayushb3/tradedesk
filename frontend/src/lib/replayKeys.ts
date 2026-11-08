/**
 * The one replay keymap, used by every player (trade/day replay, free replay, Learn drills,
 * the scenario author):
 *   Space        play / pause
 *   ← / →        one candle
 *   Shift+← / →  one second (where the player has second bars; otherwise one candle)
 *   ↑ / ↓        speed ×2 / ÷2
 * Keys are ignored while typing, inside a dialog, or with Ctrl/Cmd/Alt held, and never
 * scroll the page.
 */
import { useEffect, useRef } from "react";

const SUPPRESS_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

export function isInputFocused(target: EventTarget | null): boolean {
  if (!target || !(target instanceof Element)) return false;
  if (SUPPRESS_TAGS.has(target.tagName)) return true;
  if (target.hasAttribute("contenteditable")) return true;
  // Anywhere inside a dialog (shadcn Dialog/Sheet).
  if (target.closest('[role="dialog"]')) return true;
  return false;
}

/** Help text for tooltips. */
export const REPLAY_KEYS_HELP =
  "←/→ one candle · Shift+←/→ one second · ↑/↓ speed ×2 / ÷2 · Space play/pause";

export type ReplayKey =
  | { kind: "toggle" }
  | { kind: "step"; dir: 1 | -1; fine: boolean }
  | { kind: "speed"; dir: 1 | -1 }
  | { kind: "end" };

/** What a keydown means to a player, or null when it is not a replay key (pure, for checks). */
export function replayKeyOf(e: {
  key: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
}): ReplayKey | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  switch (e.key) {
    case " ":
      return { kind: "toggle" };
    case "ArrowRight":
      return { kind: "step", dir: 1, fine: Boolean(e.shiftKey) };
    case "ArrowLeft":
      return { kind: "step", dir: -1, fine: Boolean(e.shiftKey) };
    case "ArrowUp":
      return { kind: "speed", dir: 1 };
    case "ArrowDown":
      return { kind: "speed", dir: -1 };
    case "End":
      return { kind: "end" };
    default:
      return null;
  }
}

export interface ReplayKeyHandlers {
  onToggle?: () => void;
  /** `fine`: Shift was held (one second where the player supports it). */
  onStep?: (dir: 1 | -1, fine: boolean) => void;
  onSpeed?: (dir: 1 | -1) => void;
  onEnd?: () => void;
}

/**
 * Bind the replay keys on window. Only the handlers given are bound; a key without a handler
 * passes through untouched. Handlers are read fresh on every key, so callers need not memoize.
 */
export function useReplayKeys(handlers: ReplayKeyHandlers, enabled = true): void {
  const ref = useRef(handlers);
  ref.current = handlers;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (isInputFocused(e.target)) return;
      const k = replayKeyOf(e);
      if (!k) return;
      const h = ref.current;
      if (k.kind === "toggle" && h.onToggle) {
        e.preventDefault();
        h.onToggle();
      } else if (k.kind === "step" && h.onStep) {
        e.preventDefault();
        h.onStep(k.dir, k.fine);
      } else if (k.kind === "speed" && h.onSpeed) {
        e.preventDefault();
        h.onSpeed(k.dir);
      } else if (k.kind === "end" && h.onEnd) {
        e.preventDefault();
        h.onEnd();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}
