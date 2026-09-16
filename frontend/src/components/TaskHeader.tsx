import { useState } from "react";
import { rollbackTask, stopTask } from "../api/client";
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
  // P1-B: rollback needs a settled task — the backend answers 409 otherwise.
  const settled = task.status === "COMPLETED" || task.status === "FAILED" || task.status === "INTERRUPTED";
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const logout = useAuthStore((s) => s.logout);
  const [confirmRollback, setConfirmRollback] = useState(false);
  const [rollbackBusy, setRollbackBusy] = useState(false);
  const [rollbackNote, setRollbackNote] = useState<string | null>(null);

  const onStop = async () => {
    try {
      await stopTask(task.id);
    } catch {
      /* ignore */
    }
  };

  const onRollback = async () => {
    setRollbackBusy(true);
    try {
      const r = await rollbackTask(task.id);
      const data = r?.data;
      if (data?.ok) {
        setRollbackNote(
          data.already_original
            ? "已是原样（无可回滚的改动）"
            : `已回滚 ${data.files.length} 个文件`
        );
      } else {
        // FastAPI error bodies carry {detail} instead of the envelope: a 409
        // (task still active) is the rejection the user can act on.
        const detail = (r as unknown as { detail?: string }).detail;
        setRollbackNote(detail ? "回滚被拒绝（任务尚未收尾）" : "回滚请求失败");
      }
    } catch {
      setRollbackNote("回滚请求失败");
    } finally {
      setRollbackBusy(false);
      setConfirmRollback(false);
    }
  };

  return (
    <header className="px-4 py-3 border-b border-slate-800 flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <div className="font-semibold truncate">{task.title || "未命名任务"}</div>
        <div className="text-xs text-slate-500 truncate">{task.user_input}</div>
      </div>
      {rollbackNote && (
        <span className="text-xs text-slate-400 truncate max-w-[16rem]">{rollbackNote}</span>
      )}
      <span
        className={`text-xs px-2 py-1 rounded text-white ${
          STATUS_STYLE[task.status] || "bg-slate-600"
        }`}
      >
        {STATUS_LABEL[task.status] || task.status}
      </span>
      {settled && !confirmRollback && (
        <button
          onClick={() => {
            setRollbackNote(null);
            setConfirmRollback(true);
          }}
          className="text-xs px-3 py-1 rounded bg-slate-700 hover:bg-slate-600 text-white"
          title="把沙箱恢复到任务开始前（只动文件，不动断点）"
        >
          回滚
        </button>
      )}
      {settled && confirmRollback && (
        <span className="flex items-center gap-2 text-xs">
          <span className="text-amber-300">恢复到任务开始前？</span>
          <button
            onClick={onRollback}
            disabled={rollbackBusy}
            className="px-3 py-1 rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50"
          >
            {rollbackBusy ? "回滚中…" : "确认回滚"}
          </button>
          <button
            onClick={() => setConfirmRollback(false)}
            disabled={rollbackBusy}
            className="px-3 py-1 rounded bg-slate-700 hover:bg-slate-600 text-white disabled:opacity-50"
          >
            取消
          </button>
        </span>
      )}
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
