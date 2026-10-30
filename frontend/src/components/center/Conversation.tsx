import type { Task } from "../../types";
import { isPlanNotice, legacySteps, splitRounds, stepsForRound } from "../../lib/rounds";
import { AnswerSheet } from "./AnswerSheet";
import { FailureBlock } from "./FailureBlock";
import { PlanAnchor } from "./PlanAnchor";
import { TurnProcess } from "./TurnProcess";
import { UserBubble } from "./UserBubble";

interface ConversationProps {
  task: Task;
  onOpenPlan?: () => void;
}

export function Conversation({ task, onOpenPlan }: ConversationProps) {
  const conversation = task.conversation.length
    ? task.conversation
    : task.user_input
      ? [{ role: "user" as const, content: task.user_input }]
      : [];
  const rounds = splitRounds(conversation);
  const legacy = legacySteps(task.steps);
  const empty = rounds.length === 0 && task.steps.length === 0;
  const running = task.status === "RUNNING";
  const knownStepIndexes = new Set(task.steps.map((step) => step.index));

  return (
    <div
      id="conversation"
      tabIndex={-1}
      role="region"
      aria-label="对话记录"
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4"
    >
      <div className="flex flex-col gap-4">
        {empty && (
          <p className="text-body text-tertiary">
            还没有内容。在下方描述一个任务开始。
          </p>
        )}

        {rounds.map((round, i) => {
          // Index 0 is a leading assistant-only block, not a backend round.
          const roundSteps = round.index > 0 ? stepsForRound(task.steps, round.index) : [];
          const last = i === rounds.length - 1;
          return (
            <div key={round.index} className="flex flex-col gap-3">
              {round.user && <UserBubble text={round.user.content} />}
              {last && onOpenPlan && <PlanAnchor task={task} onOpenPlan={onOpenPlan} />}
              {roundSteps.length > 0 && (
                <TurnProcess
                  taskId={task.id}
                  steps={roundSteps}
                  live={running && last}
                  includeLooseMarkers={last}
                  knownStepIndexes={knownStepIndexes}
                />
              )}
              {round.answers.map((turn, j) =>
                isPlanNotice(turn.content) ? (
                  <div key={j} className="px-1 text-meta text-tertiary">
                    {turn.content}
                  </div>
                ) : (
                  <AnswerSheet key={j} content={turn.content} />
                )
              )}
            </div>
          );
        })}

        {legacy.length > 0 && (
          <TurnProcess
            taskId={task.id}
            steps={legacy}
            title="历史执行过程"
            includeLooseMarkers={rounds.length === 0}
            knownStepIndexes={knownStepIndexes}
          />
        )}

        <FailureBlock task={task} />
      </div>
    </div>
  );
}
