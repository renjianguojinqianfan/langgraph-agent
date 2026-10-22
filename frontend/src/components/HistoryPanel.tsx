import { useTaskStore } from "../store/taskStore";
import { TaskStatus } from "../types";

const STATUS_STYLE: Record<TaskStatus, string> = {
  PENDING: "bg-slate-500",
  RUNNING: "bg-blue-500 animate-pulse",
  FAILED: "bg-red-600",
  INTERRUPTED: "bg-amber-500",
};

const STATUS_LABEL: Record<TaskStatus, string> = {
  PENDING: "等待中",
  RUNNING: "运行中",
  FAILED: "失败",
  INTERRUPTED: "停下",
};

export function HistoryPanel() {
  const tasks = useTaskStore((s) => s.tasks);
  const current = useTaskStore((s) => s.currentTaskId);
  const setCurrent = useTaskStore((s) => s.setCurrentTask);
  const filter = useTaskStore((s) => s.historyFilter);
  const setFilter = useTaskStore((s) => s.setHistoryFilter);

  return (
    <aside className="w-64 shrink-0 bg-panel border-r border-slate-800 flex flex-col">
      <div className="px-3 py-3 border-b border-slate-800 font-semibold flex items-center justify-between">
        <span>历史任务</span>
        <button
          className="text-xs text-sky-400 hover:text-sky-300"
          onClick={() => setCurrent(null)}
        >
          + 新建
        </button>
      </div>
      <div className="px-2 py-2 border-b border-slate-800 flex gap-1 text-xs">
        <button
          onClick={() => setFilter("current")}
          className={`px-2 py-1 rounded ${
            filter === "current" ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          当前目录
        </button>
        <button
          onClick={() => setFilter("all")}
          className={`px-2 py-1 rounded ${
            filter === "all" ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          全部
        </button>
      </div>
      <div className="flex-1 overflow-auto p-2 space-y-1">
        {tasks.length === 0 && (
          <div className="text-slate-500 text-sm p-2">暂无任务，在下方输入开始。</div>
        )}
        {tasks.map((t) => (
          <button
            key={t.id}
            onClick={() => setCurrent(t.id)}
            className={`w-full text-left text-sm rounded px-2 py-2 hover:bg-panelMuted/60 ${
              current === t.id ? "bg-panelMuted" : ""
            }`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`inline-block w-2 h-2 rounded-full ${
                  STATUS_STYLE[t.status] || "bg-slate-600"
                }`}
              />
              <span className="truncate flex-1">{t.title || t.user_input}</span>
            </div>
            <div className="text-[11px] text-slate-500 mt-0.5">
              {STATUS_LABEL[t.status] || t.status}
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}
