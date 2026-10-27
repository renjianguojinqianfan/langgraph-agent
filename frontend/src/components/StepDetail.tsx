import { useTaskStore } from "../store/taskStore";
import { Task } from "../types";

function eventTime(ts?: number): string {
  if (!ts) return "--:--:--";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

export function StepDetail({ task }: { task: Task | null }) {
  const events = useTaskStore((s) => s.events);

  return (
    <aside className="w-full shrink-0 bg-surface-column border-l border-rule flex flex-col">
      <div className="border-b border-rule px-3 py-3 text-meta text-secondary">
        事件日志
      </div>
      <div className="flex-1 space-y-1 overflow-auto p-3">
        {events.length === 0 ? (
          <div className="text-body text-tertiary">
            {task ? "还没有事件；任务开始后这里会出现。" : "选择一个任务查看事件。"}
          </div>
        ) : (
          events
            .slice(-30)
            .reverse()
            .map((e, i) => (
              <div
                key={i}
                className="flex gap-2 font-mono text-machine text-tertiary"
              >
                <span className="shrink-0 tabular-nums">{eventTime(e.ts)}</span>
                <span className="truncate">{e.type}</span>
              </div>
            ))
        )}
      </div>
    </aside>
  );
}
