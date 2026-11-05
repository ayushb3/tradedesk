import { Link } from "react-router-dom";
import { ExternalLink } from "lucide-react";

import type { DrillBecause } from "@/lib/api";
import { shortDate } from "@/lib/learn";
import { lessonPath, tradePath } from "@/lib/learnRoutes";
import { cn } from "@/lib/utils";

/**
 * A lesson title that opens the in-app reader (/knowledge/lessons/:id) when the lesson is
 * ingested, plain text otherwise. The course video stays reachable as a small secondary link.
 */
export function LessonLink({
  title,
  readerId,
  videoUrl,
  className,
}: {
  title: string;
  readerId?: number | null;
  videoUrl?: string | null;
  className?: string;
}) {
  return (
    <span className={cn("flex min-w-0 items-center justify-between gap-2", className)}>
      {readerId != null ? (
        <Link to={lessonPath(readerId)} className="min-w-0 truncate text-primary hover:underline">
          {title}
        </Link>
      ) : (
        <span className="min-w-0 truncate" title="Not in the lesson reader yet">
          {title}
        </span>
      )}
      {videoUrl ? (
        <a
          href={videoUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex shrink-0 items-center gap-1 text-label text-muted-foreground hover:text-foreground hover:underline"
        >
          Video <ExternalLink className="h-3 w-3" />
        </a>
      ) : null}
    </span>
  );
}

/** "Because of your trade on Oct 27" linking to that trade's review. */
export function BecauseLink({ because, className }: { because: DrillBecause; className?: string }) {
  return (
    <Link
      to={tradePath(because.trade_id)}
      className={cn("text-label text-primary hover:underline", className)}
    >
      Because of your trade on {shortDate(because.date)}
    </Link>
  );
}
