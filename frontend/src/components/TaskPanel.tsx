import { WorkspaceFiles } from "./WorkspaceFiles";
import { PlanPanel } from "./PlanPanel";
import { SubtaskList } from "./SubtaskList";
import { TraceTab } from "./TraceTab";
import { Composer } from "./center/Composer";
import { Conversation } from "./center/Conversation";
import { TaskHeader } from "./center/TaskHeader";
import { Task, TaskViewTab } from "../types";

interface TaskPanelProps {
  task: Task | null;
  tab: TaskViewTab;
  onTabChange: (tab: TaskViewTab) => void;
  rightOpen: boolean;
  onToggleRight: () => void;
}

export function TaskPanel({
  task,
  tab,
  onTabChange,
  rightOpen,
  onToggleRight,
}: TaskPanelProps) {
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
          <button
            onClick={() => onTabChange("trace")}
            className={`px-3 py-2 -mb-px border-b-2 text-meta transition ${
              tab === "trace"
                ? "border-signal-now text-primary"
                : "border-transparent text-tertiary hover:text-secondary"
            }`}
          >
            Trace / 运行日志
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        {task ? (
          tab === "trace" ? (
            <div className="flex-1 overflow-auto p-4">
              <TraceTab taskId={task.id} />
            </div>
          ) : tab === "subtask" ? (
            <div className="flex-1 space-y-3 overflow-auto p-4">
              <div className="text-meta text-secondary">子任务</div>
              <SubtaskList subtasks={task.subtasks ?? []} />
            </div>
          ) : (
            <Conversation
              task={task}
              after={
                <>
                  <PlanPanel key={task.id} task={task} />
                  <WorkspaceFiles taskId={task.id} status={task.status} />
                </>
              }
            />
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
