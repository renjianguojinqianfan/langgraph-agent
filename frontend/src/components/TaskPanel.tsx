import { Composer } from "./center/Composer";
import { Conversation } from "./center/Conversation";
import { SubagentStrip } from "./center/SubagentStrip";
import { TaskHeader } from "./center/TaskHeader";
import { TraceFace } from "./dash/TraceFace";
import { Task } from "../types";

interface TaskPanelProps {
  task: Task | null;
  rightOpen: boolean;
  onToggleRight: () => void;
  onOpenPlan: () => void;
  traceOpen: boolean;
  onCloseTrace: () => void;
}

export function TaskPanel({
  task,
  rightOpen,
  onToggleRight,
  onOpenPlan,
  traceOpen,
  onCloseTrace,
}: TaskPanelProps) {
  if (task && traceOpen) {
    return (
      <section className="flex min-h-0 flex-1 flex-col min-w-0">
        <TaskHeader
          task={task}
          rightOpen={rightOpen}
          onToggleRight={onToggleRight}
        />
        <TraceFace taskId={task.id} onClose={onCloseTrace} />
      </section>
    );
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col min-w-0">
      {task ? (
        <TaskHeader
          task={task}
          rightOpen={rightOpen}
          onToggleRight={onToggleRight}
        />
      ) : (
        <header className="flex h-[52px] shrink-0 items-center border-b border-rule px-4 text-title text-primary">
          新任务
        </header>
      )}

      {task && task.mode === "build" && (
        <SubagentStrip subtasks={task.subtasks ?? []} />
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        {task ? (
          <Conversation task={task} onOpenPlan={onOpenPlan} />
        ) : (
          <div className="flex flex-1 items-center justify-center p-4 text-body text-tertiary">
            在下方输入任务开始，或在左侧选择历史任务。
          </div>
        )}
      </div>

      <Composer />
    </section>
  );
}
