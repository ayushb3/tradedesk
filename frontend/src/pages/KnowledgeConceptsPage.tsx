/**
 * KnowledgeConceptsPage — /knowledge/concepts (M8c)
 *
 * The concept index: every concept your course material teaches, grouped by
 * category (e.g. Strategy / Risk / Psychology / Process). A
 * client-side text filter narrows by name, alias, or definition. Each concept
 * card links to its detail page and carries a lesson-count badge — framed as
 * *how much of the playbook touches this idea*, never a score.
 *
 * Graceful empty state: when the concepts pass hasn't been run the backend
 * returns `available: false` with guidance (run `make knowledge-concepts`), surfaced
 * as an Alert rather than an error — mirrors the M8a/M8b Knowledge pages.
 */

import { LearnTabs } from "@/components/knowledge/LearnTabs";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Network, Search, ServerCrash } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type KnowledgeConceptSummary } from "@/lib/api";
import { useKnowledgeConcepts } from "@/lib/queries";

/** Display label for a raw category key ("Trade_Mgmt" → "Trade Mgmt"). */
function categoryLabel(category: string): string {
  return category.replace(/_/g, " ");
}

function ConceptCard({ concept }: { concept: KnowledgeConceptSummary }) {
  return (
    <Link to={`/knowledge/concepts/${concept.slug}`} className="block">
      <Card className="h-full transition-colors hover:bg-accent/30">
        <CardContent className="flex h-full flex-col gap-2 p-4">
          <div className="flex items-start justify-between gap-3">
            <span className="font-medium leading-tight text-foreground/90">
              {concept.name}
            </span>
            <Badge variant="muted" className="tabular shrink-0">
              {concept.lesson_count} {concept.lesson_count === 1 ? "lesson" : "lessons"}
            </Badge>
          </div>
          <p className="line-clamp-3 text-xs leading-relaxed text-muted-foreground">
            {concept.definition}
          </p>
          {concept.aliases.length > 0 ? (
            <p className="mt-auto truncate pt-1 text-label text-muted-foreground/70">
              aka {concept.aliases.join(", ")}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </Link>
  );
}

export function KnowledgeConceptsPage() {
  const concepts = useKnowledgeConcepts();
  const [filter, setFilter] = useState("");
  const data = concepts.data;
  const notGenerated = data && !data.available;

  const filtered = useMemo(() => {
    const all = data?.concepts ?? [];
    const q = filter.trim().toLowerCase();
    if (!q) return all;
    return all.filter((c) => {
      const haystack = [c.name, c.definition, c.category, ...c.aliases]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [data, filter]);

  // Preserve the API's category-then-name ordering while grouping into sections.
  const grouped = useMemo(() => {
    const groups: { category: string; concepts: KnowledgeConceptSummary[] }[] = [];
    for (const c of filtered) {
      const last = groups[groups.length - 1];
      if (last && last.category === c.category) {
        last.concepts.push(c);
      } else {
        groups.push({ category: c.category, concepts: [c] });
      }
    }
    return groups;
  }, [filtered]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4">
        <h1 className="font-semibold tracking-tight">Learn</h1>
        <LearnTabs />
        {data && data.available ? (
          <span className="ml-auto text-xs text-muted-foreground">
            {data.concept_count} concepts
          </span>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-4xl flex-col gap-4">
          {concepts.isLoading ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 9 }).map((_, i) => (
                <Skeleton key={i} className="h-28 w-full rounded-lg" />
              ))}
            </div>
          ) : concepts.isError ? (
            <Alert variant="destructive">
              <ServerCrash className="h-4 w-4" />
              <AlertTitle>Could not load concepts</AlertTitle>
              <AlertDescription>
                {concepts.error instanceof ApiError
                  ? concepts.error.detail ?? concepts.error.message
                  : "Unexpected error. Is the data service running?"}
              </AlertDescription>
            </Alert>
          ) : notGenerated ? (
            <Alert>
              <Network className="h-4 w-4" />
              <AlertTitle>Concepts not generated yet</AlertTitle>
              <AlertDescription>
                {data?.detail ??
                  "The concept index has not been built yet."}
              </AlertDescription>
            </Alert>
          ) : (
            <>
              <div className="flex flex-col gap-2">
                <p className="text-xs text-muted-foreground">
                  The ideas the playbook teaches, grouped by theme. Open a
                  concept to see its definition and the lessons that cover it.
                </p>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="Filter concepts… e.g. patience, risk, FOMO"
                    className="pl-9"
                  />
                </div>
              </div>

              {grouped.length === 0 ? (
                <p className="mt-6 text-center text-sm text-muted-foreground">
                  {filter.trim()
                    ? `No concepts match “${filter.trim()}”.`
                    : "No concepts found."}
                </p>
              ) : (
                grouped.map((group) => (
                  <section key={group.category} className="flex flex-col gap-2">
                    <h2 className="text-label font-semibold uppercase tracking-widest text-muted-foreground/60">
                      {categoryLabel(group.category)}
                    </h2>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      {group.concepts.map((c) => (
                        <ConceptCard key={c.slug} concept={c} />
                      ))}
                    </div>
                  </section>
                ))
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
