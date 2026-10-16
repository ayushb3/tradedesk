/**
 * AnswerKeyCard — shows the correct actions + explanation after the drill
 * finishes. Only rendered in the "finished" state.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AnswerKey, AttemptAction } from "@/lib/trainingTypes";
import { cn } from "@/lib/utils";

interface AnswerKeyCardProps {
  answerKey: AnswerKey;
  userActions: AttemptAction[];
  className?: string;
}

function actionLabel(action: string): string {
  const labels: Record<string, string> = {
    enter_long: "Enter Long",
    enter_short: "Enter Short",
    skip: "Skip",
    wait: "Wait",
    exit: "Exit",
    set_stop: "Set Stop",
    move_stop: "Move Stop",
  };
  return labels[action] ?? action;
}

function actionColor(action: string): string {
  if (action === "enter_long") return "bg-emerald-600/20 text-emerald-400 border-emerald-600/30";
  if (action === "enter_short") return "bg-rose-600/20 text-rose-400 border-rose-600/30";
  return "";
}

export function AnswerKeyCard({
  answerKey,
  userActions,
  className,
}: AnswerKeyCardProps) {
  const userDecisionActions = userActions.filter(
    (a) => a.action === "enter_long" || a.action === "enter_short" || a.action === "skip",
  );

  return (
    <Card className={cn("", className)}>
      <CardHeader className="pb-2">
        <CardTitle>Answer Key</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Correct actions */}
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Correct
          </p>
          <div className="flex flex-wrap gap-2">
            {answerKey.correct_actions.map((a) => (
              <span
                key={a}
                className={cn(
                  "rounded-md border px-3 py-1 text-sm font-medium",
                  actionColor(a) || "bg-secondary text-secondary-foreground border-border",
                )}
              >
                {actionLabel(a)}
              </span>
            ))}
          </div>
        </div>

        {/* User's decisions */}
        {userDecisionActions.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Your decision
            </p>
            <div className="flex flex-wrap gap-2">
              {userDecisionActions.map((a, i) => {
                const isCorrect = answerKey.correct_actions.includes(a.action as never);
                return (
                  <span
                    key={i}
                    className={cn(
                      "rounded-md border px-3 py-1 text-sm font-medium",
                      isCorrect
                        ? "border-emerald-600/30 bg-emerald-600/10 text-emerald-400"
                        : "border-rose-600/30 bg-rose-600/10 text-rose-400",
                    )}
                  >
                    {actionLabel(a.action)}
                    <span className="ml-1.5 text-[10px] opacity-70">
                      bar {a.bar}
                    </span>
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* Explanation */}
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Explanation
          </p>
          <p className="text-sm leading-relaxed text-foreground">
            {answerKey.explanation}
          </p>
        </div>

        {/* Management notes if present */}
        {answerKey.management_notes.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Management notes
            </p>
            <ul className="space-y-1">
              {answerKey.management_notes.map((note, i) => (
                <li key={i} className="text-sm text-muted-foreground">
                  {typeof note === "object" && note !== null && "note" in note
                    ? String((note as Record<string, unknown>).note)
                    : JSON.stringify(note)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
