import { SubtaskList } from "./SubtaskList";
import { Composer } from "./center/Composer";
import { Conversation } from "./center/Conversation";
import { TaskHeader } from "./center/TaskHeader";
import { TraceFace } from "./dash/TraceFace";
import { Task } from "../types";

interface TaskPanelProps {
  task: Task | null;
  /** Run / subagent tabs; F7 moves the subagent face to a strip. */
  tab: "run" | "subtask";
  onTabChange: (tab: "run" | "subtask") => void;
  rightOpen: boolean;
  onToggleRight: () => void;
  onOpenPlan: () => void;
  traceOpen: boolean;
  onCloseTrace: () => void;
}

export function TaskPanel({
  task,
  tab,
  onTabChange,
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

      {task && (
        <div className="flex items-center border-b border-rule px-4">
          <button
            onClick={() => onTabChange("run")}
            className={`px-3 py-2 -mb-px border-b-2 text-meta transition ${
              tab === "run"
                ? "border-signal-now text-primary"
                : "border-transparent text-tertiary hover:text-secondary"
            }`}
          >
            运行
          </button>
          <button
            onClick={() => onTabChange("subtask")}
            className={`px-3 py-2 -mb-px border-b-2 text-meta transition ${
              tab === "subtask"
                ? "border-signal-now text-primary"
                : "border-transparent text-tertiary hover:text-secondary"
            }`}
          >
            子任务
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        {task ? (
          tab === "subtask" ? (
            <div className="flex-1 space-y-3 overflow-auto p-4">
              <div className="text-meta text-secondary">子任务</div>
              <SubtaskList subtasks={task.subtasks ?? []} />
            </div>
          ) : (
            <Conversation task={task} onOpenPlan={onOpenPlan} />
          )
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
