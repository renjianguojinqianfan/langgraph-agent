import { SubTask } from "../types";

const STATUS_STYLES: Record<string, string> = {
  completed: "bg-green-500/15 text-green-300",
  running: "bg-sky-500/15 text-sky-300",
  failed: "bg-red-500/15 text-red-300",
  pending: "bg-slate-500/15 text-slate-300",
};

/** P1 item 2: aggregated sub-agent cards (subtask_start/result/failed). */
export function SubtaskList({ subtasks }: { subtasks: SubTask[] }) {
  if (!subtasks || subtasks.length === 0) {
    return (
      <div className="text-sm text-slate-500">
        {subtasks ? "暂无子任务" : ""}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {subtasks.map((st) => (
        <div
          key={st.subtask_id}
          className="rounded border border-slate-800 bg-slate-900 p-2 text-sm"
        >
          <div className="flex items-center gap-2">
            <span className="font-medium">{st.name}</span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] ${
                STATUS_STYLES[st.status] ?? STATUS_STYLES.pending
              }`}
            >
              {st.status}
            </span>
          </div>
          {st.summary && (
            <div className="text-slate-300 mt-1 text-xs">{st.summary}</div>
          )}
          {st.error && <div className="text-red-400 mt-1 text-xs">{st.error}</div>}
          {st.artifacts.length > 0 && (
            <div className="text-slate-500 mt-1 font-mono text-[11px]">
              {st.artifacts.join(", ")}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
