/**
 * LearnPage: /learn, the one home for learning.
 *
 *   1. Today's drills      the nightly set; a drill picked because of one of your trades says so
 *   2. From your trades    detected leaks + scenario drills Critique / Debrief made from your trades
 *   3. Progress            the topic map (click a topic for its drills and lessons) + discipline
 *   4. Lessons             search; each lesson opens the in-app reader
 *
 * A score is information, not a grade: neutral colours, no XP. When TRADEDESK_LEARN_DIR is unset
 * the nightly-drill parts show one calm notice; the training parts still work.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, FolderOpen, Play, Search, ServerCrash } from "lucide-react";

import { LearnTabs } from "@/components/knowledge/LearnTabs";
import { BecauseLink, LessonLink } from "@/components/learn/LessonLink";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { DrillPublic, LearnLesson, LearnTopic } from "@/lib/api";
import { useActiveScenarios, useLeaks, useProgress } from "@/lib/drills";
import { drillWhen, isNotConfigured, scoreText, shortDate, topicName } from "@/lib/learn";
import {
  LEARN_LEAKS,
  LEARN_PROGRESS,
  LEARN_SCENARIOS,
  practicePath,
  topicPath,
  tradePath,
  tradeScenarios,
} from "@/lib/learnRoutes";
import { useLearnLessons, useLearnToday, useLearnTopics } from "@/lib/queries";

function NotConfigured({ message }: { message?: string }) {
  return (
    <Alert>
      <FolderOpen className="h-4 w-4" />
      <AlertTitle>The learn folder is not set up</AlertTitle>
      <AlertDescription>
        Nightly drills are built into a private folder. Set <code className="num">TRADEDESK_LEARN_DIR</code> to that
        folder and restart the backend; nothing else changes until then.
        {message ? <span className="mt-1 block text-label text-muted-foreground">Server says: {message}</span> : null}
      </AlertDescription>
    </Alert>
  );
}

function SectionHead({ title, link }: { title: string; link?: { to: string; label: string } }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 className="text-value font-semibold">{title}</h2>
      {link ? (
        <Link to={link.to} className="inline-flex items-center gap-0.5 text-label text-primary hover:underline">
          {link.label} <ChevronRight className="h-3.5 w-3.5" />
        </Link>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- today's drills

export function DrillCard({ drill }: { drill: DrillPublic }) {
  const done = drill.status === "done";
  return (
    <Card className="relative h-full transition-colors hover:bg-accent/40">
      <CardContent className="flex h-full flex-col gap-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <span className="label-caps text-label text-muted-foreground">{topicName(drill.topic)}</span>
          <Badge variant={done ? "default" : "muted"}>{done ? `Done, ${scoreText(drill.score)}` : "New"}</Badge>
        </div>
        <div className="num text-value font-semibold">{drill.ticker}</div>
        <p className="text-label text-muted-foreground">{drillWhen(drill)}</p>
        {drill.because ? <BecauseLink because={drill.because} className="relative z-10 w-fit" /> : null}
        {/* the stretched link makes the whole card open the drill; the "because" link sits above it */}
        <Link
          to={`/learn/drills/${encodeURIComponent(drill.id)}`}
          className="mt-auto pt-1 text-body text-primary after:absolute after:inset-0 after:rounded-lg hover:underline focus-visible:outline-none focus-visible:after:ring-1 focus-visible:after:ring-ring"
        >
          {done ? "Review the answer" : "Start drill"}
        </Link>
      </CardContent>
    </Card>
  );
}

function TodaySection({ notConfigured, message }: { notConfigured: boolean; message?: string }) {
  const q = useLearnToday();
  let body: React.ReactNode;
  if (notConfigured) body = <NotConfigured message={message} />;
  else if (q.isLoading) body = <Skeleton className="h-32 w-full" />;
  else if (q.isError) body = <ErrorNote what="today's drills" />;
  else if (!q.data || isNotConfigured(q.data)) body = null;
  else if (q.data.drills.length === 0)
    body = (
      <Card>
        <CardContent className="p-4 text-body text-muted-foreground">
          No drills yet. The nightly builder makes tomorrow&apos;s set at 21:00 PT.
        </CardContent>
      </Card>
    );
  else
    body = (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {q.data.drills.map((d) => (
          <DrillCard key={d.id} drill={d} />
        ))}
      </div>
    );
  const setDate = q.data && !isNotConfigured(q.data) ? q.data.set_date : null;
  return (
    <section className="flex flex-col gap-3" aria-label="Today's drills">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-value font-semibold">Today&apos;s drills</h2>
        {setDate ? <span className="text-label text-muted-foreground">Set for {shortDate(setDate)} (PT date)</span> : null}
      </div>
      {body}
    </section>
  );
}

// ---------------------------------------------------------------- from your trades

const LEAK_NAMES: Record<string, string> = {
  revenge_trading: "Revenge trading",
  chop_overtrading: "Chop overtrading",
  moved_stops: "Moved stops",
  psychology_loss: "Psychology failures",
};

function LeaksList() {
  const leaks = useLeaks();
  if (leaks.isLoading) return <Skeleton className="h-16 w-full" />;
  if (leaks.isError) return <p className="text-label text-muted-foreground">Leaks could not be loaded.</p>;
  const list = leaks.data?.leaks ?? [];
  if (list.length === 0) return <p className="text-body text-muted-foreground">No leaks detected in your recent trades.</p>;
  return (
    <ul className="divide-y divide-border">
      {list.map((l) => {
        const [first, ...rest] = l.prescribed_scenario_ids;
        return (
          <li key={l.name} className="flex items-center justify-between gap-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-body font-medium">{LEAK_NAMES[l.name] ?? l.name}</p>
              <p className="truncate text-label text-muted-foreground">{l.description}</p>
            </div>
            {first != null ? (
              <Link
                to={practicePath(first, rest.length ? `playlist=${rest.join(",")}` : "")}
                className="inline-flex shrink-0 items-center gap-1 text-body text-primary hover:underline"
              >
                <Play className="h-3.5 w-3.5" /> Drill it
              </Link>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function TradeDrillsList() {
  const scenarios = useActiveScenarios();
  if (scenarios.isLoading) return <Skeleton className="h-16 w-full" />;
  if (scenarios.isError) return <p className="text-label text-muted-foreground">Drills could not be loaded.</p>;
  const list = tradeScenarios(scenarios.data ?? []).slice(0, 8);
  if (list.length === 0)
    return (
      <p className="text-body text-muted-foreground">
        None yet. Critique or Debrief a trade and create its drill; it shows up here.
      </p>
    );
  return (
    <ul className="divide-y divide-border">
      {list.map((s) => (
        <li key={s.id} className="flex items-center justify-between gap-3 py-2">
          <div className="min-w-0">
            <Link to={practicePath(s.id)} className="block truncate text-body text-primary hover:underline">
              {s.title}
            </Link>
            {s.source_trade_id != null ? (
              <Link to={tradePath(s.source_trade_id)} className="text-label text-muted-foreground hover:underline">
                From trade #{s.source_trade_id}
              </Link>
            ) : (
              <span className="text-label text-muted-foreground">From a debrief</span>
            )}
          </div>
          <Link
            to={practicePath(s.id)}
            className="inline-flex shrink-0 items-center gap-1 text-body text-primary hover:underline"
          >
            <Play className="h-3.5 w-3.5" /> Start
          </Link>
        </li>
      ))}
    </ul>
  );
}

function FromTradesSection() {
  return (
    <section className="flex flex-col gap-3" aria-label="From your trades">
      <SectionHead title="From your trades" />
      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardContent className="flex flex-col gap-2 p-4">
            <SectionHead title="Leaks" link={{ to: LEARN_LEAKS, label: "Evidence" }} />
            <LeaksList />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex flex-col gap-2 p-4">
            <SectionHead title="Drills from Critique and Debrief" link={{ to: LEARN_SCENARIOS, label: "All drills" }} />
            <TradeDrillsList />
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- progress

function TopicTile({ topic }: { topic: LearnTopic }) {
  return (
    <Link
      to={topicPath(topic.slug)}
      className="group block rounded-lg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
    >
      <Card className="h-full transition-colors group-hover:bg-accent/40">
        <CardContent className="flex flex-col gap-1 p-3">
          <span className="text-body font-medium group-hover:underline">{topic.title || topicName(topic.slug)}</span>
          <span className="text-label text-muted-foreground">
            {topic.drills_done} {topic.drills_done === 1 ? "drill" : "drills"} done
          </span>
          <span className="text-label text-muted-foreground">
            Average {topic.avg_score == null ? "n/a" : <span className="num text-foreground">{Math.round(topic.avg_score)}</span>}
            {topic.last_date ? `, last ${shortDate(topic.last_date)}` : ""}
          </span>
        </CardContent>
      </Card>
    </Link>
  );
}

function DisciplineLine() {
  const p = useProgress();
  if (p.isLoading || p.isError || !p.data) return null;
  const due = p.data.due_reviews.length;
  return (
    <p className="text-body text-muted-foreground">
      Discipline streak <span className="num text-foreground">{p.data.process_streak}</span>, longest{" "}
      <span className="num text-foreground">{p.data.longest_process_streak}</span>
      {due ? (
        <>
          {" · "}
          <span className="num text-foreground">{due}</span> {due === 1 ? "review" : "reviews"} due
        </>
      ) : null}
    </p>
  );
}

function ProgressSection({ notConfigured }: { notConfigured: boolean }) {
  const q = useLearnTopics();
  const data = q.data;
  return (
    <section className="flex flex-col gap-3" aria-label="Progress">
      <SectionHead title="Progress" link={{ to: LEARN_PROGRESS, label: "Mastery and quests" }} />
      <DisciplineLine />
      {notConfigured ? null : q.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : q.isError ? (
        <ErrorNote what="the topic map" />
      ) : !data || isNotConfigured(data) ? null : data.length === 0 ? (
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

// ---------------------------------------------------------------- lessons

export function LessonRow({ lesson }: { lesson: LearnLesson }) {
  return (
    <li className="flex flex-col py-2">
      <LessonLink title={lesson.title} readerId={lesson.reader_id} videoUrl={lesson.video_url} className="text-body" />
      {lesson.module ? <p className="truncate text-label text-muted-foreground">Module {lesson.module}</p> : null}
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

export function ErrorNote({ what }: { what: string }) {
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
      <TodaySection
        notConfigured={notConfigured}
        message={today.data && isNotConfigured(today.data) ? today.data.message : undefined}
      />
      <FromTradesSection />
      <ProgressSection notConfigured={notConfigured} />
      {notConfigured ? null : <LessonSection />}
    </div>
  );
}
