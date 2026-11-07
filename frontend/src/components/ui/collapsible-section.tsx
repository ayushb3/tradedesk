import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { useSectionOpen } from "@/lib/collapse";
import { cn } from "@/lib/utils";

/**
 * A section whose header line toggles its body. Closed, it is one line: the title plus a
 * short summary, so long pages stay short. The open state is remembered per viewer (`id`).
 */
export function CollapsibleSection({
  id,
  title,
  summary,
  icon,
  defaultOpen = true,
  actions,
  className,
  children,
  testId,
}: {
  id: string;
  title: string;
  summary?: ReactNode;
  icon?: ReactNode;
  defaultOpen?: boolean;
  /** Controls on the header's right; clicks on them do not toggle the section. */
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
  testId?: string;
}) {
  const [open, setOpen] = useSectionOpen(id, defaultOpen);
  const bodyId = `section-${id}`;
  return (
    <section className={cn("space-y-2", className)} data-testid={testId} data-open={open}>
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen(!open)}
          className="group flex min-w-0 flex-1 items-center gap-2 rounded-md py-1 text-left hover:text-foreground"
        >
          <ChevronRight
            className={cn(
              "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90",
            )}
          />
          {icon}
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {summary != null && (
            <span className="min-w-0 truncate text-label text-muted-foreground">{summary}</span>
          )}
        </button>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {open && <div id={bodyId}>{children}</div>}
    </section>
  );
}
