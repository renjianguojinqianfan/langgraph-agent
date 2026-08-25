import { stopTask } from "../api/client";
import { useAuthStore } from "../store/authStore";
import { Task, TaskStatus } from "../types";

const STATUS_STYLE: Record<TaskStatus, string> = {
  PENDING: "bg-slate-500",
  RUNNING: "bg-blue-500",
  COMPLETED: "bg-green-600",
  FAILED: "bg-red-600",
  INTERRUPTED: "bg-amber-500",
};

const STATUS_LABEL: Record<TaskStatus, string> = {
  PENDING: "等待中",
  RUNNING: "运行中",
  COMPLETED: "已完成",
  FAILED: "失败",
  INTERRUPTED: "已停止",
};

export function TaskHeader({ task }: { task: Task }) {
  const running = task.status === "RUNNING";
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const logout = useAuthStore((s) => s.logout);

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
      </div>
      <span
        className={`text-xs px-2 py-1 rounded text-white ${
          STATUS_STYLE[task.status] || "bg-slate-600"
        }`}
      >
        {STATUS_LABEL[task.status] || task.status}
      </span>
      {running && (
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
    </header>
  );
}
