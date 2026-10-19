/**
 * KnowledgeLessonPage — /knowledge/lessons/:id (M8b)
 *
 * The Lesson Reader: a lesson's raw transcript, segmented into deep-linkable
 * chunk sections using the char-offset anchors from the API. The header carries
 * the module/lesson citation, an original-lesson link-out (when a URL template is configured), prev/next
 * navigation, and a read/starred progress toggle.
 *
 * Progress toggles are neutral, process-framed actions — "Mark read" / "Star" —
 * with no streaks, points, or outcome framing (docs/M6-ANTI-GAMBLING-AUDIT.md).
 *
 * Study sheets (M8c PR-2): when the response carries a non-null `sheet`, we
 * render it ABOVE the transcript — summary, rules, an optional Mermaid diagram,
 * and a self-check quiz. When `sheet` is null the reader looks exactly as it did
 * in M8b (nothing extra rendered). Concepts (M8c PR-1) are rendered as chips
 * below the header, linking into the concept index.
 *
 * A missing lesson id returns a 404 from the backend, surfaced as a "not found"
 * Alert rather than a crash.
 */

import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  BookMarked,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Star,
} from "lucide-react";

import { MermaidDiagram } from "@/components/knowledge/MermaidDiagram";
import { LessonQuiz } from "@/components/knowledge/LessonQuiz";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type KnowledgeChunkAnchor, type KnowledgeLessonSheet } from "@/lib/api";
import { useKnowledgeLesson, useSetLessonProgress } from "@/lib/queries";

/**
 * Split raw transcript text into non-overlapping display segments keyed to the
 * chunk anchors. Chunks may overlap (windowed chunking), so we advance a cursor
 * and only render the not-yet-emitted slice for each chunk, anchoring each
 * segment at its chunk `seq` for deep-linking. Any leading/trailing text not
 * covered by a chunk is still shown.
 */
function segmentTranscript(
  raw: string,
  chunks: KnowledgeChunkAnchor[],
): { seq: number; text: string }[] {
  if (chunks.length === 0) {
    return raw.trim() ? [{ seq: 0, text: raw }] : [];
  }
  const ordered = [...chunks].sort((a, b) => a.char_start - b.char_start);
  const segments: { seq: number; text: string }[] = [];
  let cursor = 0;

  for (const c of ordered) {
    const start = Math.max(cursor, c.char_start);
    const end = Math.min(raw.length, c.char_end);
    if (end <= start) continue;
    const text = raw.slice(start, end);
    if (text.length > 0) segments.push({ seq: c.seq, text });
    cursor = end;
  }
  if (cursor < raw.length) {
    const tail = raw.slice(cursor);
    if (tail.trim()) {
      const lastSeq = ordered[ordered.length - 1]?.seq ?? 0;
      segments.push({ seq: lastSeq + 1, text: tail });
    }
  }
  return segments;
}

/**
 * The generated study sheet, rendered above the transcript when present.
 * Summary + rules use `whitespace-pre-wrap` (no markdown dep) so bullets and
 * paragraphs read fine; the diagram and quiz are their own components. The quiz
 * is a self-check — no scoring is kept.
 */
function StudySheet({ sheet }: { sheet: KnowledgeLessonSheet }) {
  const hasSummary = sheet.summary_md.trim().length > 0;
  const hasRules = sheet.rules_md.trim().length > 0;
  const hasDiagram = sheet.diagram_mermaid != null && sheet.diagram_mermaid.trim().length > 0;
  const hasQuiz = sheet.quiz.length > 0;

  return (
    <Card>
      <CardContent className="flex flex-col gap-6 p-5">
        <div className="flex items-center gap-2">
          <span className="text-label uppercase tracking-widest text-muted-foreground/60">
            Study sheet
          </span>
          <span className="text-label text-muted-foreground/40">
            AI-distilled · verify against the lesson
          </span>
        </div>

        {hasSummary ? (
          <section className="flex flex-col gap-1.5">
            <h2 className="text-sm font-semibold text-foreground">Summary</h2>
            <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
              {sheet.summary_md.trim()}
            </div>
          </section>
        ) : null}

        {hasRules ? (
          <section className="flex flex-col gap-1.5">
            <h2 className="text-sm font-semibold text-foreground">Rules</h2>
            <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
              {sheet.rules_md.trim()}
            </div>
          </section>
        ) : null}

        {hasDiagram ? (
          <section className="flex flex-col gap-1.5">
            <h2 className="text-sm font-semibold text-foreground">Diagram</h2>
            <MermaidDiagram source={sheet.diagram_mermaid as string} />
          </section>
        ) : null}

        {hasQuiz ? (
          <section className="flex flex-col gap-3">
            <div className="flex flex-col gap-0.5">
              <h2 className="text-sm font-semibold text-foreground">Self-check</h2>
              <p className="text-xs text-muted-foreground">
                Not graded — pick an answer to see the reasoning.
              </p>
            </div>
            <LessonQuiz questions={sheet.quiz} />
          </section>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function KnowledgeLessonPage() {
  const { id } = useParams<{ id: string }>();
  const numericId = id ? Number(id) : null;
  const navigate = useNavigate();

  const lesson = useKnowledgeLesson(numericId);
  const setProgress = useSetLessonProgress();
  const data = lesson.data;

  const segments = useMemo(
    () => (data ? segmentTranscript(data.raw_text, data.chunks) : []),
    [data],
  );

  const notFound =
    lesson.isError && lesson.error instanceof ApiError && lesson.error.status === 404;

  const status = data?.progress.status ?? "unread";
  const busy = setProgress.isPending;

  const setStatus = (next: "read" | "starred") => {
    if (numericId == null) return;
    // Toggle off back to unread when re-clicking the active state.
    const target = status === next ? "unread" : next;
    setProgress.mutate({ id: numericId, status: target });
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Link
          to="/knowledge/modules"
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <BookMarked className="h-4 w-4" />
          Modules
        </Link>
        {data ? (
          <span className="truncate text-xs text-muted-foreground">
            {data.citation}
          </span>
        ) : null}

        {data ? (
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant={status === "read" ? "default" : "outline"}
              size="sm"
              disabled={busy}
              onClick={() => setStatus("read")}
            >
              Mark read
            </Button>
            <Button
              variant={status === "starred" ? "default" : "outline"}
              size="sm"
              disabled={busy}
              onClick={() => setStatus("starred")}
            >
              <Star
                className={
                  status === "starred"
                    ? "mr-1 h-3.5 w-3.5 fill-current"
                    : "mr-1 h-3.5 w-3.5"
                }
              />
              Star
            </Button>
          </div>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {lesson.isLoading ? (
            <>
              <Skeleton className="h-8 w-2/3 rounded-md" />
              <Skeleton className="h-4 w-1/3 rounded-md" />
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full rounded-lg" />
              ))}
            </>
          ) : notFound ? (
            <Alert>
              <BookMarked className="h-4 w-4" />
              <AlertTitle>Lesson not found</AlertTitle>
              <AlertDescription>
                {lesson.error instanceof ApiError
                  ? lesson.error.detail ?? `Lesson ${id} does not exist.`
                  : `Lesson ${id} does not exist.`}{" "}
                <Link to="/knowledge/modules" className="text-primary hover:underline">
                  Back to Modules
                </Link>
              </AlertDescription>
            </Alert>
          ) : lesson.isError ? (
            <Alert variant="destructive">
              <BookMarked className="h-4 w-4" />
              <AlertTitle>Could not load lesson</AlertTitle>
              <AlertDescription>
                {lesson.error instanceof ApiError
                  ? lesson.error.detail ?? lesson.error.message
                  : "Unexpected error. Is the data service running?"}
              </AlertDescription>
            </Alert>
          ) : data ? (
            <>
              <div className="flex flex-col gap-1.5">
                <h1 className="text-2xl font-semibold tracking-tight">{data.title}</h1>
                <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                  <span>{data.citation}</span>
                  {data.source_url ? (
                    <a
                      href={data.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      Open original lesson <ExternalLink className="h-3 w-3" />
                    </a>
                  ) : null}
                </div>
              </div>

              {data.concepts.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-label uppercase tracking-widest text-muted-foreground/60">
                    Concepts
                  </span>
                  {data.concepts.map((c) => (
                    <Link
                      key={c.slug}
                      to={`/knowledge/concepts/${c.slug}`}
                      className="rounded-full border border-border px-2 py-0.5 text-xs text-foreground/90 transition-colors hover:bg-accent/50"
                    >
                      {c.name}
                    </Link>
                  ))}
                </div>
              ) : null}

              {data.sheet != null ? <StudySheet sheet={data.sheet} /> : null}

              <Card>
                <CardContent className="flex flex-col gap-5 p-5">
                  {segments.length > 0 ? (
                    segments.map((seg) => (
                      <section
                        key={seg.seq}
                        id={`chunk-${seg.seq}`}
                        className="scroll-mt-20 whitespace-pre-wrap text-sm leading-relaxed text-foreground/90"
                      >
                        {seg.text}
                      </section>
                    ))
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      This lesson has no transcript text.
                    </p>
                  )}
                </CardContent>
              </Card>

              <nav className="flex items-center justify-between pt-1">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={data.prev_lesson_id == null}
                  onClick={() =>
                    data.prev_lesson_id != null &&
                    navigate(`/knowledge/lessons/${data.prev_lesson_id}`)
                  }
                >
                  <ChevronLeft className="mr-1 h-4 w-4" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={data.next_lesson_id == null}
                  onClick={() =>
                    data.next_lesson_id != null &&
                    navigate(`/knowledge/lessons/${data.next_lesson_id}`)
                  }
                >
                  Next
                  <ChevronRight className="ml-1 h-4 w-4" />
                </Button>
              </nav>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
