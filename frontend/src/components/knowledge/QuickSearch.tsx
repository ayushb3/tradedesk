/**
 * QuickSearch — global ⌘K / Ctrl+K command palette over the course curriculum.
 *
 * A window-level keydown listener toggles the dialog from any page. On open it
 * shows a CommandDialog with a search input; typing (debounced ~250ms) drives
 * the existing `useKnowledgeSearch` hook. Results list ranked passages with their
 * lesson breadcrumb; selecting one navigates to the lesson reader and closes.
 *
 * Mounted once globally in AppShell so the shortcut works everywhere.
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BookMarked } from "lucide-react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useKnowledgeSearch } from "@/lib/queries";

export function QuickSearch() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [debounced, setDebounced] = useState("");
  const navigate = useNavigate();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Global ⌘K / Ctrl+K toggle.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Debounce the input into the query the hook actually fires on.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setDebounced(input.trim()), 250);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [input]);

  // Reset state whenever the palette closes.
  useEffect(() => {
    if (!open) {
      setInput("");
      setDebounced("");
    }
  }, [open]);

  const search = useKnowledgeSearch(debounced, 8);
  const results = search.data?.available ? search.data.results : [];

  const goTo = (lessonId: number) => {
    setOpen(false);
    navigate(`/knowledge/lessons/${lessonId}`);
  };

  const hasQuery = debounced.length > 0;

  return (
    <CommandDialog open={open} onOpenChange={setOpen} label="Search the curriculum">
      <CommandInput
        autoFocus
        value={input}
        onValueChange={setInput}
        placeholder="Search the course curriculum… (⌘K)"
      />
      <CommandList>
        {!hasQuery ? (
          <div className="py-6 text-center text-sm text-muted-foreground">
            Type to search lessons by meaning.
          </div>
        ) : search.isLoading || search.isFetching ? (
          <div className="py-6 text-center text-sm text-muted-foreground">
            Searching…
          </div>
        ) : results.length === 0 ? (
          <CommandEmpty>No passages found for “{debounced}”.</CommandEmpty>
        ) : (
          <CommandGroup heading={`${results.length} passages`}>
            {results.map((hit) => (
              <CommandItem
                key={`${hit.lesson_id}-${hit.char_start}`}
                value={`${hit.lesson_id}-${hit.char_start}`}
                onSelect={() => goTo(hit.lesson_id)}
                className="flex-col items-start gap-1 py-2"
              >
                <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <BookMarked className="h-3 w-3 shrink-0" />
                  <span className="truncate">{hit.citation}</span>
                </div>
                <span className="line-clamp-2 text-sm text-foreground/90">
                  {hit.text}
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  );
}
