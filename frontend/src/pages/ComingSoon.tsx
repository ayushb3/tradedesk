import type { LucideIcon } from "lucide-react";

interface ComingSoonProps {
  title: string;
  description: string;
  icon: LucideIcon;
  milestone: string;
}

/** Shared empty shell for routes not built yet (Journal, Daily Review). */
export function ComingSoon({
  title,
  description,
  icon: Icon,
  milestone,
}: ComingSoonProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center border-b border-border px-4">
        <h1 className="font-semibold tracking-tight">{title}</h1>
      </div>
      <div className="flex flex-1 items-center justify-center">
        <div className="flex max-w-sm flex-col items-center gap-3 text-center">
          <div className="rounded-full border border-border bg-card p-4">
            <Icon className="h-6 w-6 text-muted-foreground" />
          </div>
          <p className="text-sm text-muted-foreground">{description}</p>
          <span className="rounded-full border border-border px-2 py-0.5 text-label uppercase tracking-wide text-muted-foreground">
            {milestone}
          </span>
        </div>
      </div>
    </div>
  );
}
