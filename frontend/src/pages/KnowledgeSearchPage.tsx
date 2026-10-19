/**
 * KnowledgeSearchPage — /knowledge/search (M8a)
 *
 * Minimal semantic search over your course material. A query box submits to
 * GET /api/knowledge/search and renders ranked transcript passages, each with a
 * lesson/module breadcrumb citation and a relevance score. The full Lesson
 * Reader + Concepts views are M8b/M8c; this view is intentionally lean.
 *
 * Graceful empty state: when the KB hasn't been ingested the backend returns
 * `available: false` with guidance (run `make knowledge-ingest`), which we surface
 * instead of an error — the app is fully usable with ingestion never run.
 */

import { LearnTabs } from "@/components/knowledge/LearnTabs";
import { useState } from "react";
import { BookMarked, Search, ServerCrash, ExternalLink } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type KnowledgeSearchHit } from "@/lib/api";
import { useKnowledgeSearch, useKnowledgeStatus } from "@/lib/queries";

const EXAMPLES = ["confirmation candles", "support levels", "support and resistance", "position sizing"];

function ScoreBadge({ score }: { score: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  return (
    <Badge variant="muted" className="tabular shrink-0">
      {pct}% match
    </Badge>
  );
}

function HitCard({ hit }: { hit: KnowledgeSearchHit }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <BookMarked className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{hit.citation}</span>
          </div>
          <ScoreBadge score={hit.score} />
        </div>
        <p className="text-sm leading-relaxed text-foreground/90">{hit.text}</p>
        {hit.source_url ? (
          <a
            href={hit.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex w-fit items-center gap-1 text-xs text-primary hover:underline"
          >
            Open original lesson <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function KnowledgeSearchPage() {
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState("");

  const status = useKnowledgeStatus();
  const search = useKnowledgeSearch(submitted);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(input.trim());
  };

  const runExample = (q: string) => {
    setInput(q);
    setSubmitted(q);
  };

  const data = search.data;
  const notIngested = data && !data.available;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4">
        <h1 className="font-semibold tracking-tight">Learn</h1>
        <LearnTabs />
        {status.data ? (
          <span className="ml-auto text-xs text-muted-foreground">
            {status.data.ingested
              ? `${status.data.lesson_count} lessons`
              : "not ingested"}
          </span>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          <form onSubmit={onSubmit} className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                autoFocus
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask your course notes… e.g. what does the course say about confirmation candles?"
                className="pl-9"
              />
            </div>
            <Button type="submit" disabled={!input.trim()}>
              Search
            </Button>
          </form>

          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span>Try:</span>
            {EXAMPLES.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => runExample(q)}
                className="rounded-full border border-border px-2 py-0.5 hover:bg-accent/50"
              >
                {q}
              </button>
            ))}
          </div>

          {/* States */}
          {submitted === "" ? (
            <p className="mt-6 text-center text-sm text-muted-foreground">
              Search your course material by meaning. Results cite the exact
              module and lesson.
            </p>
          ) : search.isLoading ? (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-24 w-full rounded-lg" />
              ))}
            </div>
          ) : search.isError ? (
            <Alert variant="destructive">
              <ServerCrash className="h-4 w-4" />
              <AlertTitle>Search failed</AlertTitle>
              <AlertDescription>
                {search.error instanceof ApiError
                  ? search.error.detail ?? search.error.message
                  : "Unexpected error. Is the data service running?"}
              </AlertDescription>
            </Alert>
          ) : notIngested ? (
            <Alert>
              <BookMarked className="h-4 w-4" />
              <AlertTitle>Knowledge base not ingested</AlertTitle>
              <AlertDescription>
                {data?.detail ??
                  "The lessons have not been loaded yet, so there is nothing to search."}
              </AlertDescription>
            </Alert>
          ) : data && data.results.length === 0 ? (
            <p className="mt-6 text-center text-sm text-muted-foreground">
              No passages found for “{submitted}”.
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-xs text-muted-foreground">
                {data?.results.length} passages for “{submitted}”
              </p>
              {data?.results.map((hit) => (
                <HitCard key={`${hit.lesson_id}-${hit.char_start}`} hit={hit} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
