import { stopTask } from "../../api/client";
import { Task, TaskMode } from "../../types";
import { Button, IconButton } from "../ui/Button";
import { Status } from "../ui/Status";

interface TaskHeaderProps {
  task: Task;
  rightOpen: boolean;
  onToggleRight: () => void;
}

const MODE_LABEL: Record<TaskMode, string> = {
  plan: "计划",
  build: "执行",
};

export function TaskHeader({ task, rightOpen, onToggleRight }: TaskHeaderProps) {
  // #189: PENDING is the startup transient — it is stoppable like RUNNING.
  const stoppable = task.status === "RUNNING" || task.status === "PENDING";
  const heading = task.title || task.user_input || "未命名任务";
  const workspace = task.workspace || "默认";

  const onStop = async () => {
    try {
      await stopTask(task.id);
    } catch {
      /* the snapshot remains authoritative */
    }
  };

  return (
    <header className="flex h-[52px] shrink-0 items-center gap-3 border-b border-rule px-4">
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-title text-primary" title={heading}>
          {heading}
        </h1>
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-meta text-secondary">
            {MODE_LABEL[task.mode]}
          </span>
          <span
            className="truncate font-mono text-machine text-tertiary"
            title={workspace}
          >
            {workspace}
          </span>
        </div>
      </div>
      <Status status={task.status} />
      {stoppable && (
        <Button variant="danger" size="sm" onClick={onStop}>
          停止
        </Button>
      )}
      <span className="hidden max-[1180px]:inline-flex">
        <IconButton
          type="button"
          aria-label="打开工作区"
          aria-controls="task-dash"
          aria-expanded={rightOpen}
          icon="panel"
          onClick={onToggleRight}
        />
      </span>
    </header>
  );
}
