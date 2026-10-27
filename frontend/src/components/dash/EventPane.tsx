import { useTaskStore } from "../../store/taskStore";
import { compressFaceParts, type SSEvent, type Task } from "../../types";

const TYPE_LABEL: Record<string, string> = {
  task_created: "任务创建",
  task_resumed: "任务续跑",
  step_start: "步骤开始",
  tool_call: "工具调用",
  tool_result: "工具结果",
  tool_result_evicted: "结果被驱逐",
  context_compressed: "上下文压缩",
  final_answer: "最终回答",
  task_failed: "任务失败",
  task_interrupted: "任务停下",
  subtask_start: "子任务开始",
  subtask_result: "子任务结果",
  subtask_failed: "子任务失败",
  heartbeat: "心跳",
};

function summary(ev: SSEvent): string {
  const d = ev.data as Record<string, unknown> | null | undefined;
  if (!d || typeof d !== "object") return "";
  switch (ev.type) {
    case "step_start":
      return `步骤 #${String(d.index ?? "?")}`;
    case "tool_call":
    case "tool_result":
      return typeof d.tool_name === "string" ? d.tool_name : "";
    case "context_compressed":
      return compressFaceParts(d);
    case "task_failed":
      return typeof d.error === "string" ? d.error : "";
    case "subtask_start":
    case "subtask_result":
    case "subtask_failed":
      return typeof d.name === "string" ? d.name : "";
    default:
      return "";
  }
}

function eventTime(ts?: number): string {
  if (!ts || !Number.isFinite(ts)) return "--:--:--";
  const d = new Date(ts * 1000);
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/**
 * SSE replays its buffer on every reconnect; the replayed frame is the same
 * fact again. Keep the latest arrival per payload (arrival ts always differs)
 * so the pane shows each fact once.
 */
function dedupe(events: SSEvent[]): SSEvent[] {
  const latest = new Map<string, { ev: SSEvent; at: number }>();
  events.forEach((ev, at) => {
    latest.set(`${ev.type}:${JSON.stringify(ev.data)}`, { ev, at });
  });
  return [...latest.values()]
    .sort((a, b) => a.at - b.at)
    .map((entry) => entry.ev);
}

export function EventPane({ task }: { task: Task | null }) {
  const eventsByTask = useTaskStore((s) => s.eventsByTask);
  const events = task ? eventsByTask[task.id] ?? [] : [];
  const shown = dedupe(events).slice(-100).reverse();

  return (
    <div className="flex flex-col gap-3">
      <p className="text-meta text-tertiary">
        时间 = 本页收到事件的时间；事件只做旁证，状态以快照为准。事件只保存在内存里，刷新后为空。
      </p>

      {shown.length === 0 ? (
        <p className="text-body text-tertiary">
          {task
            ? "还没有事件。任务开始后这里会出现；完整回放见下方「打开完整 Trace」。"
            : "选择一个任务查看事件。"}
        </p>
      ) : (
        <ol className="flex flex-col">
          {shown.map((ev, i) => {
            const label = TYPE_LABEL[ev.type] ?? ev.type;
            const detail = summary(ev);
            return (
              <li
                key={i}
                className="flex items-baseline gap-2 border-b border-rule py-1 last:border-b-0"
              >
                <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
                  {eventTime(ev.ts)}
                </span>
                <span className="shrink-0 text-meta text-secondary">{label}</span>
                <span
                  className="min-w-0 flex-1 truncate font-mono text-machine text-tertiary"
                  title={detail || undefined}
                >
                  {detail}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
