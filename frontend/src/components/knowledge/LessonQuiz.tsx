/**
 * LessonQuiz — a client-only self-check for a lesson's study sheet.
 *
 * Each question renders its choices as buttons. Selecting one reveals whether
 * it matches the sheet's `answer_idx` and shows the explanation. This is a
 * self-check, not a graded test: NOTHING is persisted, there is no score,
 * streak, or points tally (docs/M6-ANTI-GAMBLING-AUDIT.md). Framing emphasizes
 * understanding the reasoning, not "winning".
 */

import { useState } from "react";
import { Check, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { KnowledgeQuizQuestion } from "@/lib/api";

interface LessonQuizProps {
  questions: KnowledgeQuizQuestion[];
}

function QuizItem({ question, index }: { question: KnowledgeQuizQuestion; index: number }) {
  const [selected, setSelected] = useState<number | null>(null);
  const answered = selected != null;

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-sm font-medium text-foreground/90">
        <span className="mr-1.5 text-muted-foreground">{index + 1}.</span>
        {question.q}
      </p>
      <div className="flex flex-col gap-1.5">
        {question.choices.map((choice, i) => {
          const isSelected = selected === i;
          const isCorrect = i === question.answer_idx;
          // Once answered, always mark the correct choice; mark the wrong pick.
          const revealCorrect = answered && isCorrect;
          const revealWrong = answered && isSelected && !isCorrect;
          return (
            <button
              key={i}
              type="button"
              disabled={answered}
              onClick={() => setSelected(i)}
              className={cn(
                "flex items-center gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors",
                "disabled:cursor-default",
                !answered && "border-border hover:bg-accent/50",
                revealCorrect && "border-emerald-600/60 bg-emerald-600/10 text-foreground",
                revealWrong && "border-destructive/60 bg-destructive/10 text-foreground",
                answered && !revealCorrect && !revealWrong && "border-border opacity-60",
              )}
            >
              {revealCorrect ? (
                <Check className="h-4 w-4 shrink-0 text-emerald-500" />
              ) : revealWrong ? (
                <X className="h-4 w-4 shrink-0 text-destructive" />
              ) : (
                <span className="h-4 w-4 shrink-0" />
              )}
              <span>{choice}</span>
            </button>
          );
        })}
      </div>
      {answered ? (
        <div className="flex flex-col gap-1 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">
            {selected === question.answer_idx
              ? "That's the one."
              : "Worth a second look — the reasoning matters more than the pick."}
          </span>
          {question.explanation ? <span>{question.explanation}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

export function LessonQuiz({ questions }: LessonQuizProps) {
  if (questions.length === 0) return null;
  return (
    <div className="flex flex-col gap-5">
      {questions.map((q, i) => (
        <QuizItem key={i} question={q} index={i} />
      ))}
    </div>
  );
}
