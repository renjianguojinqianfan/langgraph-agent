import { stopTask } from "../api/client";
import { useAuthStore } from "../store/authStore";
import { profileName, useModelStore } from "../store/modelStore";
import { Task } from "../types";
import { IconButton } from "./ui/Button";
import { Status } from "./ui/Status";

interface TaskHeaderProps {
  task: Task;
  rightOpen: boolean;
  onToggleRight: () => void;
}

export function TaskHeader({ task, rightOpen, onToggleRight }: TaskHeaderProps) {
  // #189: PENDING is the startup transient — it is stoppable like RUNNING.
  const stoppable = task.status === "RUNNING" || task.status === "PENDING";
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const logout = useAuthStore((s) => s.logout);
  const models = useModelStore((s) => s.models);
  const defaultId = useModelStore((s) => s.defaultId);
  const modelLabel = profileName(models, task.model || defaultId);

  const onStop = async () => {
    try {
      await stopTask(task.id);
    } catch {
      /* ignore */
    }
  };

  return (
    <header className="px-4 py-3 border-b border-slate-800 flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="font-semibold truncate">{task.title || "未命名任务"}</div>
        <div className="text-xs text-slate-500 truncate">{task.user_input}</div>
        <div className="text-[11px] text-slate-600 truncate" title={task.workspace}>
          目录：{task.workspace || "默认"}
        </div>
        <div className="text-[11px] text-slate-500 truncate" title={task.model || defaultId}>
          模型：{modelLabel}
        </div>
      </div>
      <Status status={task.status} />
      {stoppable && (
        <button
          onClick={onStop}
          className="text-xs px-3 py-1 rounded bg-red-600 hover:bg-red-500 text-white"
        >
          停止
        </button>
      )}
      {authEnabled && (
        <button
          onClick={logout}
          className="text-xs px-3 py-1 rounded bg-slate-700 hover:bg-slate-600 text-white"
        >
          登出
        </button>
      )}
      <IconButton
        type="button"
        aria-label="打开工作区"
        aria-controls="task-dash"
        aria-expanded={rightOpen}
        icon="panel"
        onClick={onToggleRight}
        className="max-[1180px]:inline-flex hidden"
      />
    </header>
  );
}
