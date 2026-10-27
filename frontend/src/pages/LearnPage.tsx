/**
 * LearnPage: /learn
 *
 * Today's drills (three cards), the topic map and the lessons list. A score is information, not a
 * grade: neutral colours, no streaks, no XP. When TRADEDESK_LEARN_DIR is unset every endpoint
 * answers `configured:false` and the page shows one calm notice.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, FolderOpen, Search, ServerCrash } from "lucide-react";

import { LearnTabs } from "@/components/knowledge/LearnTabs";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { DrillPublic, LearnLesson, LearnTopic } from "@/lib/api";
import { drillWhen, isNotConfigured, scoreText, shortDate, topicName } from "@/lib/learn";
import { useLearnLessons, useLearnToday, useLearnTopics } from "@/lib/queries";

function NotConfigured({ message }: { message?: string }) {
  return (
    <Alert>
      <FolderOpen className="h-4 w-4" />
      <AlertTitle>The learn folder is not set up</AlertTitle>
      <AlertDescription>
        Drills are built into a private folder. Set <code className="num">TRADEDESK_LEARN_DIR</code> to that folder and restart
        the backend; nothing else changes until then.
        {message ? <span className="mt-1 block text-label text-muted-foreground">Server says: {message}</span> : null}
      </AlertDescription>
    </Alert>
  );
}

function DrillCard({ drill }: { drill: DrillPublic }) {
  const done = drill.status === "done";
  return (
    <Link
      to={`/learn/drills/${encodeURIComponent(drill.id)}`}
      className="group block rounded-lg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
    >
      <Card className="h-full transition-colors group-hover:bg-accent/40">
        <CardContent className="flex h-full flex-col gap-2 p-4">
          <div className="flex items-start justify-between gap-2">
            <span className="label-caps text-label text-muted-foreground">{topicName(drill.topic)}</span>
            <div className="flex shrink-0 items-center gap-1">
              {drill.review && <Badge variant="outline">Review</Badge>}
              <Badge variant={done ? "default" : "muted"}>{done ? `Done, ${scoreText(drill.score)}` : "New"}</Badge>
            </div>
          </div>
          <div className="num text-value font-semibold">{drill.ticker}</div>
          <p className="text-label text-muted-foreground">{drillWhen(drill)}</p>
          <p className="mt-auto pt-1 text-body text-primary group-hover:underline">{done ? "Review the answer" : "Start drill"}</p>
        </CardContent>
      </Card>
    </Link>
  );
}

function TodaySection() {
  const q = useLearnToday();
  if (q.isLoading) return <Skeleton className="h-32 w-full" />;
  if (q.isError) return <ErrorNote what="today's drills" />;
  const data = q.data;
  if (!data || isNotConfigured(data)) return null; // the page-level notice covers this
  return (
    <section className="flex flex-col gap-3" aria-label="Today's drills">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-value font-semibold">Today&apos;s drills</h2>
        {data.set_date && <span className="text-label text-muted-foreground">Set for {shortDate(data.set_date)} (PT date)</span>}
      </div>
      {data.drills.length === 0 ? (
        <Card>
          <CardContent className="p-4 text-body text-muted-foreground">
            No drills yet. The nightly builder makes tomorrow&apos;s set at 21:00 PT.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {data.drills.map((d) => (
            <DrillCard key={d.id} drill={d} />
          ))}
        </div>
      )}
    </section>
  );
}

function TopicTile({ topic }: { topic: LearnTopic }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-1 p-3">
        <span className="text-body font-medium">{topic.title || topicName(topic.slug)}</span>
        <span className="text-label text-muted-foreground">
          {topic.drills_done} {topic.drills_done === 1 ? "drill" : "drills"} done
        </span>
        <span className="text-label text-muted-foreground">
          Average {topic.avg_score == null ? "n/a" : <span className="num text-foreground">{Math.round(topic.avg_score)}</span>}
          {topic.last_date ? `, last ${shortDate(topic.last_date)}` : ""}
        </span>
      </CardContent>
    </Card>
  );
}

function TopicSection() {
  const q = useLearnTopics();
  if (q.isLoading) return <Skeleton className="h-24 w-full" />;
  if (q.isError) return <ErrorNote what="the topic map" />;
  const data = q.data;
  if (!data || isNotConfigured(data)) return null;
  return (
    <section className="flex flex-col gap-3" aria-label="Topic map">
      <h2 className="text-value font-semibold">Topic map</h2>
      {data.length === 0 ? (
        <p className="text-body text-muted-foreground">No topics yet.</p>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {data.map((t) => (
            <TopicTile key={t.slug} topic={t} />
          ))}
        </div>
      )}
    </section>
  );
}

function LessonRow({ lesson }: { lesson: LearnLesson }) {
  return (
    <li className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <p className="truncate text-body">{lesson.title}</p>
        <p className="truncate text-label text-muted-foreground">{lesson.module}</p>
      </div>
      {lesson.video_url ? (
        <a
          href={lesson.video_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex shrink-0 items-center gap-1 text-body text-primary hover:underline"
        >
          Video <ExternalLink className="h-3.5 w-3.5" />
        </a>
      ) : (
        <span className="shrink-0 text-label text-muted-foreground">No video</span>
      )}
    </li>
  );
}

function LessonSection() {
  const [input, setInput] = useState("");
  const [q, setQ] = useState("");
  const lessons = useLearnLessons(q);
  const data = lessons.data;
  return (
    <section className="flex flex-col gap-3" aria-label="Lessons">
      <h2 className="text-value font-semibold">Lessons</h2>
      <form
        className="relative max-w-md"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(input.trim());
        }}
      >
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            if (e.target.value === "") setQ("");
          }}
          placeholder="Search lessons, then press Enter"
          aria-label="Search lessons"
          className="pl-9"
        />
      </form>
      {lessons.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : lessons.isError ? (
        <ErrorNote what="lessons" />
      ) : !data || isNotConfigured(data) ? null : data.length === 0 ? (
        <p className="text-body text-muted-foreground">{q ? `No lessons match "${q}".` : "No lessons yet."}</p>
      ) : (
        <Card>
          <CardContent className="p-4">
            <ul className="divide-y divide-border">
              {data.map((l) => (
                <LessonRow key={l.id} lesson={l} />
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </section>
  );
}

function ErrorNote({ what }: { what: string }) {
  return (
    <Alert variant="destructive">
      <ServerCrash className="h-4 w-4" />
      <AlertTitle>Could not load {what}</AlertTitle>
      <AlertDescription>Check that the backend is running, then reload.</AlertDescription>
    </Alert>
  );
}

export function LearnPage() {
  const today = useLearnToday();
  const notConfigured = today.data !== undefined && isNotConfigured(today.data);
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-value font-semibold">Learn</h1>
        <LearnTabs />
      </div>
      {notConfigured ? (
        <NotConfigured message={today.data && isNotConfigured(today.data) ? today.data.message : undefined} />
      ) : (
        <>
          <TodaySection />
          <TopicSection />
          <LessonSection />
        </>
      )}
    </div>
  );
}
