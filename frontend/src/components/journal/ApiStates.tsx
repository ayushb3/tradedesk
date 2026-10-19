import { AlertCircle, Inbox, ServerCrash } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";

/**
 * Full-width error card that discriminates the three failure modes M3 must
 * tolerate: backend down (status 0), endpoint not implemented (501/404), and
 * genuine errors. Shared by the journal + daily-review pages.
 */
export function ApiErrorState({ error }: { error: unknown }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const backendDown = status === 0;
  const notReady = status === 501 || status === 404;
  const message =
    error instanceof ApiError
      ? error.detail || error.message
      : error instanceof Error
        ? error.message
        : "Something went wrong.";

  return (
    <Alert
      variant={notReady ? "default" : "destructive"}
      className="max-w-xl"
    >
      {backendDown ? (
        <ServerCrash className="h-4 w-4" />
      ) : (
        <AlertCircle className="h-4 w-4" />
      )}
      <AlertTitle>
        {backendDown
          ? "Backend not running"
          : notReady
            ? "Endpoint not ready yet"
            : "Couldn't load data"}
      </AlertTitle>
      <AlertDescription>
        {backendDown ? (
          <>
            The data service isn't reachable. Start it, then reload.
          </>
        ) : notReady ? (
          "This part of the journal lights up once the backend implements it."
        ) : (
          message
        )}
      </AlertDescription>
    </Alert>
  );
}

interface EmptyStateProps {
  title: string;
  description: string;
  icon?: LucideIcon;
  action?: { label: string; onClick: () => void; loading?: boolean };
}

/** Centered empty placeholder, optionally with a primary call-to-action. */
export function EmptyState({
  title,
  description,
  icon: Icon = Inbox,
  action,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border bg-card/40 px-6 py-16 text-center">
      <div className="rounded-full border border-border bg-card p-3">
        <Icon className="h-5 w-5 text-muted-foreground" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="max-w-sm text-xs text-muted-foreground">{description}</p>
      </div>
      {action && (
        <Button onClick={action.onClick} disabled={action.loading}>
          {action.loading ? "Working…" : action.label}
        </Button>
      )}
    </div>
  );
}
