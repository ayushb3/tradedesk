/**
 * LearnTopicPage: /learn/topics/:slug
 *
 * One topic from the Learn topic map: every drill on it (all nightly sets, newest first) and the
 * lessons it covers, each opening the in-app reader. Links only; no course text is shown here.
 */
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { isNotConfigured, topicName } from "@/lib/learn";
import { LEARN_HOME } from "@/lib/learnRoutes";
import { useLearnTopic } from "@/lib/queries";
import { DrillCard, ErrorNote, LessonRow } from "@/pages/LearnPage";

export function LearnTopicPage() {
  const { slug } = useParams<{ slug: string }>();
  const q = useLearnTopic(slug);
  const data = q.data;
  const notFound = q.error instanceof ApiError && q.error.status === 404;
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link to={LEARN_HOME} className="inline-flex items-center gap-1 text-label text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" /> Learn
        </Link>
        <h1 className="text-value font-semibold">
          {data && !isNotConfigured(data) ? data.title : topicName(slug ?? "")}
        </h1>
      </div>
      {q.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : notFound ? (
        <p className="text-body text-muted-foreground">No such topic.</p>
      ) : q.isError ? (
        <ErrorNote what="this topic" />
      ) : !data || isNotConfigured(data) ? (
        <p className="text-body text-muted-foreground">The learn folder is not set up.</p>
      ) : (
        <>
          <section className="flex flex-col gap-3" aria-label="Drills">
            <h2 className="text-value font-semibold">Drills</h2>
            {data.drills.length === 0 ? (
              <p className="text-body text-muted-foreground">No drills on this topic yet.</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {data.drills.map((d) => (
                  <DrillCard key={d.id} drill={d} />
                ))}
              </div>
            )}
          </section>
          <section className="flex flex-col gap-3" aria-label="Lessons">
            <h2 className="text-value font-semibold">Lessons</h2>
            {data.lessons.length === 0 ? (
              <p className="text-body text-muted-foreground">No lessons are linked to this topic.</p>
            ) : (
              <Card>
                <CardContent className="p-4">
                  <ul className="divide-y divide-border">
                    {data.lessons.map((l) => (
                      <LessonRow key={l.id} lesson={l} />
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
          </section>
        </>
      )}
    </div>
  );
}
