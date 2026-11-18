import type { Task } from "../../types";
import { groupMarkers } from "../../lib/markers";
import { isPlanNotice, splitRounds, stepsForRound } from "../../lib/rounds";
import { useTaskStore } from "../../store/taskStore";
import { AnswerSheet } from "./AnswerSheet";
import { CompressMarker } from "./CompressMarker";
import { FailureBlock } from "./FailureBlock";
import { PlanAnchor } from "./PlanAnchor";
import { TurnProcess } from "./TurnProcess";
import { UserBubble } from "./UserBubble";

interface ConversationProps {
  task: Task;
  onOpenPlan?: () => void;
}

export function Conversation({ task, onOpenPlan }: ConversationProps) {
  const rounds = splitRounds(task.conversation);
  const markers = useTaskStore((s) => s.markers[task.id] ?? []);
  // Ownership is resolved once here; each block receives only its own markers.
  const { byStep, loose } = groupMarkers(markers, task.steps);
  const empty =
    rounds.length === 0 && task.steps.length === 0 && loose.length === 0;
  const running = task.status === "RUNNING";

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
          const roundSteps = stepsForRound(task.steps, round.index);
          const roundMarkers = roundSteps.flatMap((step) => byStep.get(step.index) ?? []);
          const last = i === rounds.length - 1;
          return (
            <div key={round.index} className="flex flex-col gap-3">
              <UserBubble text={round.user.content} />
              {roundSteps.length > 0 && (
                <TurnProcess
                  steps={roundSteps}
                  markers={roundMarkers}
                  live={running && last}
                  taskId={task.id}
                  answers={round.answers.map((turn) => turn.content)}
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

        {rounds.length > 0 && onOpenPlan && (
          <PlanAnchor task={task} onOpenPlan={onOpenPlan} />
        )}

        {loose.length > 0 && (
          <section aria-label="上下文压缩记录" className="flex flex-col">
            {loose.map((marker, i) => (
              <CompressMarker key={i} marker={marker} />
            ))}
          </section>
        )}

        <FailureBlock task={task} />
      </div>
    </div>
  );
}
