/**
 * KnowledgeConceptPage — /knowledge/concepts/:slug (M8c)
 *
 * Concept detail: the concept's name, category, definition, and aliases, plus
 * two cross-link surfaces — related-concept chips (each labelled with the
 * relation, linking to a sibling concept) and the list of lessons that teach
 * it, sorted by weight (how central the concept is to that lesson) descending.
 *
 * An unknown slug returns a 404 from the backend, surfaced as a "not found"
 * Alert rather than a crash — mirrors the Lesson Reader.
 */

import { Link, useParams } from "react-router-dom";
import { BookMarked, Network } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type KnowledgeConceptLessonRef,
  type KnowledgeConceptRelation,
  type KnowledgeRelatedConcept,
} from "@/lib/api";
import { useKnowledgeConcept } from "@/lib/queries";

/** Display label for a raw category key ("Trade_Mgmt" → "Trade Mgmt"). */
function categoryLabel(category: string): string {
  return category.replace(/_/g, " ");
}

/** Human-friendly label for a relation edge. */
const RELATION_LABEL: Record<KnowledgeConceptRelation, string> = {
  related: "related",
  prerequisite: "prerequisite",
  contrast: "contrast",
  part_of: "part of",
};

function RelatedChip({ related }: { related: KnowledgeRelatedConcept }) {
  return (
    <Link
      to={`/knowledge/concepts/${related.slug}`}
      className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs transition-colors hover:bg-accent/50"
    >
      <span className="text-foreground/90">{related.name}</span>
      <span className="text-label uppercase tracking-wide text-muted-foreground/70">
        {RELATION_LABEL[related.relation] ?? related.relation}
      </span>
    </Link>
  );
}

function LessonRow({ lesson }: { lesson: KnowledgeConceptLessonRef }) {
  return (
    <Link
      to={`/knowledge/lessons/${lesson.lesson_id}`}
      className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-accent/50"
    >
      <BookMarked className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="truncate text-foreground/90">{lesson.citation}</span>
    </Link>
  );
}

export function KnowledgeConceptPage() {
  const { slug } = useParams<{ slug: string }>();
  const concept = useKnowledgeConcept(slug);
  const data = concept.data;

  const notFound =
    concept.isError && concept.error instanceof ApiError && concept.error.status === 404;

  // Lessons ordered by weight desc (most central first); backend already sorts,
  // but we sort defensively so the UI is order-independent.
  const lessons = [...(data?.lessons ?? [])].sort((a, b) => b.weight - a.weight);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Link
          to="/knowledge/concepts"
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <Network className="h-4 w-4" />
          Concepts
        </Link>
        {data ? (
          <span className="truncate text-xs text-muted-foreground">{data.name}</span>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {concept.isLoading ? (
            <>
              <Skeleton className="h-8 w-2/3 rounded-md" />
              <Skeleton className="h-4 w-1/4 rounded-md" />
              <Skeleton className="h-24 w-full rounded-lg" />
              <Skeleton className="h-32 w-full rounded-lg" />
            </>
          ) : notFound ? (
            <Alert>
              <Network className="h-4 w-4" />
              <AlertTitle>Concept not found</AlertTitle>
              <AlertDescription>
                {concept.error instanceof ApiError
                  ? concept.error.detail ?? `Concept “${slug}” does not exist.`
                  : `Concept “${slug}” does not exist.`}{" "}
                <Link to="/knowledge/concepts" className="text-primary hover:underline">
                  Back to Concepts
                </Link>
              </AlertDescription>
            </Alert>
          ) : concept.isError ? (
            <Alert variant="destructive">
              <Network className="h-4 w-4" />
              <AlertTitle>Could not load concept</AlertTitle>
              <AlertDescription>
                {concept.error instanceof ApiError
                  ? concept.error.detail ?? concept.error.message
                  : "Unexpected error. Is the data service running?"}
              </AlertDescription>
            </Alert>
          ) : data ? (
            <>
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-2xl font-semibold tracking-tight">{data.name}</h1>
                  <Badge variant="muted">{categoryLabel(data.category)}</Badge>
                </div>
                {data.aliases.length > 0 ? (
                  <p className="text-xs text-muted-foreground">
                    Also known as {data.aliases.join(", ")}
                  </p>
                ) : null}
              </div>

              <Card>
                <CardContent className="p-5">
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
                    {data.definition}
                  </p>
                </CardContent>
              </Card>

              {data.related.length > 0 ? (
                <section className="flex flex-col gap-2">
                  <h2 className="text-label font-semibold uppercase tracking-widest text-muted-foreground/60">
                    Related concepts
                  </h2>
                  <div className="flex flex-wrap gap-2">
                    {data.related.map((r) => (
                      <RelatedChip key={`${r.slug}-${r.relation}`} related={r} />
                    ))}
                  </div>
                </section>
              ) : null}

              <section className="flex flex-col gap-2">
                <h2 className="text-label font-semibold uppercase tracking-widest text-muted-foreground/60">
                  Lessons that cover this ({data.lesson_count})
                </h2>
                {lessons.length > 0 ? (
                  <Card>
                    <CardContent className="flex flex-col gap-0.5 p-1.5">
                      {lessons.map((l) => (
                        <LessonRow key={l.lesson_id} lesson={l} />
                      ))}
                    </CardContent>
                  </Card>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No lessons are linked to this concept yet.
                  </p>
                )}
              </section>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
