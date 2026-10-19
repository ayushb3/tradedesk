/**
 * KnowledgeModulesPage — /knowledge/modules (M8b)
 *
 * The course curriculum tree: modules (in the order the API returns them, which is
 * numeric-aware label order) render as collapsible sections showing coverage
 * (read/starred counts), each expanding to its lessons. Lesson rows link to the
 * Lesson Reader (/knowledge/lessons/:id) and carry a small status indicator
 * (unread / read / starred).
 *
 * Coverage is framed as *process* — how much of the playbook you've worked
 * through — never as a score or streak (docs/M6-ANTI-GAMBLING-AUDIT.md).
 *
 * Graceful empty state: when the KB hasn't been ingested the backend returns
 * `available: false` with guidance (run `make knowledge-ingest`), surfaced as an
 * Alert rather than an error — mirrors the M8a Search page.
 */

import { LearnTabs } from "@/components/knowledge/LearnTabs";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  BookMarked,
  ChevronDown,
  ChevronRight,
  Circle,
  CheckCircle2,
  ServerCrash,
  Star,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type KnowledgeLessonStatus, type KnowledgeModule, type KnowledgeModuleLesson } from "@/lib/api";
import { useKnowledgeModules } from "@/lib/queries";

function StatusIndicator({ status }: { status: KnowledgeLessonStatus }) {
  if (status === "starred") {
    return <Star className="h-3.5 w-3.5 shrink-0 fill-amber-400 text-amber-400" aria-label="Starred" />;
  }
  if (status === "read") {
    return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" aria-label="Read" />;
  }
  return <Circle className="h-3.5 w-3.5 shrink-0 text-muted-foreground/40" aria-label="Unread" />;
}

function LessonRow({ lesson }: { lesson: KnowledgeModuleLesson }) {
  return (
    <Link
      to={`/knowledge/lessons/${lesson.id}`}
      className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-accent/50"
    >
      <StatusIndicator status={lesson.status} />
      <span className="tabular text-xs text-muted-foreground">
        {String(lesson.seq).padStart(3, "0")}
      </span>
      <span className="truncate text-foreground/90">{lesson.title}</span>
    </Link>
  );
}

function ModuleSection({ module, defaultOpen }: { module: KnowledgeModule; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <Card>
      <CardContent className="p-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-accent/30"
          aria-expanded={open}
        >
          <Chevron className="h-4 w-4 shrink-0 text-muted-foreground" />
          <BookMarked className="h-4 w-4 shrink-0 text-primary" />
          <span className="font-medium">{module.label}</span>
          <div className="ml-auto flex items-center gap-1.5">
            <Badge variant="muted" className="tabular">
              {module.read_count}/{module.lesson_count} read
            </Badge>
            {module.starred_count > 0 ? (
              <Badge variant="muted" className="tabular gap-1">
                <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
                {module.starred_count}
              </Badge>
            ) : null}
          </div>
        </button>
        {open ? (
          <div className="flex flex-col gap-0.5 border-t border-border px-2 py-1.5">
            {module.lessons.map((lesson) => (
              <LessonRow key={lesson.id} lesson={lesson} />
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function KnowledgeModulesPage() {
  const modules = useKnowledgeModules();
  const data = modules.data;
  const notIngested = data && !data.available;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4">
        <h1 className="font-semibold tracking-tight">Learn</h1>
        <LearnTabs />
        {data && data.available ? (
          <span className="ml-auto text-xs text-muted-foreground">
            {data.lesson_count} lessons · {data.module_count} modules
          </span>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-3">
          {modules.isLoading ? (
            Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-lg" />
            ))
          ) : modules.isError ? (
            <Alert variant="destructive">
              <ServerCrash className="h-4 w-4" />
              <AlertTitle>Could not load the curriculum</AlertTitle>
              <AlertDescription>
                {modules.error instanceof ApiError
                  ? modules.error.detail ?? modules.error.message
                  : "Unexpected error. Is the data service running?"}
              </AlertDescription>
            </Alert>
          ) : notIngested ? (
            <Alert>
              <BookMarked className="h-4 w-4" />
              <AlertTitle>Knowledge base not ingested</AlertTitle>
              <AlertDescription>
                {data?.detail ??
                  "The lessons have not been loaded yet."}
              </AlertDescription>
            </Alert>
          ) : data && data.modules.length === 0 ? (
            <p className="mt-6 text-center text-sm text-muted-foreground">
              No modules found.
            </p>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Work through the playbook module by module. Open a lesson to read
                the transcript and mark your progress.
              </p>
              {data?.modules.map((module, i) => (
                <ModuleSection key={module.module} module={module} defaultOpen={i === 0} />
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
