import { useTaskStore } from "../../store/taskStore";
import { compressFaceParts, type Task, type TraceMarker } from "../../types";
import { isPlanNotice, splitRounds, stepsRoundIndex } from "../../lib/rounds";
import { AnswerSheet } from "./AnswerSheet";
import { FailureBlock } from "./FailureBlock";
import { PlanAnchor } from "./PlanAnchor";
import { RoundDivider } from "./RoundDivider";
import { StepRow } from "./StepRow";
import { UserBubble } from "./UserBubble";

function CompressMarker({ marker }: { marker: TraceMarker }) {
  const d = marker.data;
  return (
    <div className="px-1 py-1 text-machine text-tertiary">
      上下文已压缩：{compressFaceParts(d)}
      {d.strategy ? `，策略 ${d.strategy}` : ""}
      {d.context_tokens != null ? `（剩余 ${d.context_tokens} tokens）` : ""}
    </div>
  );
}

function StepsBlock({ task }: { task: Task }) {
  const markers = useTaskStore((s) => s.markers[task.id] ?? []);
  const byStep = new Map<number, TraceMarker[]>();
  const loose: TraceMarker[] = [];
  for (const marker of markers) {
    const index = marker.data.step_index;
    if (typeof index === "number" && task.steps.some((s) => s.index === index)) {
      const list = byStep.get(index) ?? [];
      list.push(marker);
      byStep.set(index, list);
    } else {
      loose.push(marker);
    }
  }
  if (!task.steps.length && !loose.length) return null;

  return (
    <section aria-label="步骤" className="border-y border-rule">
      {task.steps.map((step) => (
        <div key={step.index}>
          <StepRow step={step} />
          {(byStep.get(step.index) ?? []).map((marker, i) => (
            <CompressMarker key={i} marker={marker} />
          ))}
        </div>
      ))}
      {loose.map((marker, i) => (
        <CompressMarker key={i} marker={marker} />
      ))}
    </section>
  );
}

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
  const stepsAt = stepsRoundIndex(rounds);
  const empty = rounds.length === 0 && task.steps.length === 0;

  return (
    <div
      role="region"
      aria-label="对话记录"
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4"
    >
      <div className="flex flex-col gap-3">
        {empty && (
          <p className="text-body text-tertiary">
            还没有内容。在下方描述一个任务开始。
          </p>
        )}

        {rounds.map((round, i) => (
          <div key={round.index} className="flex flex-col gap-3">
            <RoundDivider n={round.index} />
            {round.user && <UserBubble text={round.user.content} />}
            {i === stepsAt && onOpenPlan && (
              <PlanAnchor task={task} onOpenPlan={onOpenPlan} />
            )}
            {i === stepsAt && <StepsBlock task={task} />}
            {round.answers.map((turn, j) =>
              isPlanNotice(turn.content) ? (
                <div key={j} className="px-1 text-meta text-tertiary">
                  {turn.content}
                </div>
              ) : (
                <AnswerSheet key={j} task={task} content={turn.content} />
              )
            )}
          </div>
        ))}

        {rounds.length === 0 && task.steps.length > 0 && <StepsBlock task={task} />}

        <FailureBlock task={task} />
      </div>
    </div>
  );
}
