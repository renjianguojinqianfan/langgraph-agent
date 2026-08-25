import { useEffect, useMemo, useState } from "react";
import { getTaskTrace, TraceFetchError } from "../api/client";
import { SSEvent } from "../types";

type TraceCategory = "all" | "tool" | "circuit" | "compress" | "task";

interface TraceMeta {
  label: string;
  badge: string;
  category: TraceCategory;
}

const TYPE_META: Record<string, TraceMeta> = {
  task_created: {
    label: "任务创建",
    badge: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    category: "task",
  },
  plan_update: {
    label: "规划更新",
    badge: "bg-sky-500/15 text-sky-300 border-sky-500/40",
    category: "task",
  },
  step_start: {
    label: "步骤开始",
    badge: "bg-blue-500/15 text-blue-300 border-blue-500/40",
    category: "task",
  },
  tool_call: {
    label: "工具调用",
    badge: "bg-cyan-500/15 text-cyan-300 border-cyan-500/40",
    category: "tool",
  },
  tool_result: {
    label: "工具结果",
    badge: "bg-teal-500/15 text-teal-300 border-teal-500/40",
    category: "tool",
  },
  tool_circuit_open: {
    label: "熔断",
    badge: "bg-red-500/15 text-red-300 border-red-500/40",
    category: "circuit",
  },
  context_compressed: {
    label: "上下文压缩",
    badge: "bg-violet-500/15 text-violet-300 border-violet-500/40",
    category: "compress",
  },
  human_confirm_required: {
    label: "人工确认",
    badge: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    category: "task",
  },
  artifact_created: {
    label: "产物",
    badge: "bg-green-500/15 text-green-300 border-green-500/40",
    category: "task",
  },
  final_answer: {
    label: "最终回答",
    badge: "bg-green-500/15 text-green-300 border-green-500/40",
    category: "task",
  },
  task_completed: {
    label: "完成",
    badge: "bg-green-500/15 text-green-300 border-green-500/40",
    category: "task",
  },
  task_failed: {
    label: "失败",
    badge: "bg-red-500/15 text-red-300 border-red-500/40",
    category: "task",
  },
  task_interrupted: {
    label: "中断",
    badge: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    category: "task",
  },
  trace_end: {
    label: "回放结束",
    badge: "bg-slate-500/15 text-slate-300 border-slate-500/40",
    category: "task",
  },
  heartbeat: {
    label: "心跳",
    badge: "bg-slate-500/10 text-slate-400 border-slate-500/30",
    category: "task",
  },
};

const FILTERS: { key: TraceCategory; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "tool", label: "工具" },
  { key: "circuit", label: "熔断" },
  { key: "compress", label: "压缩" },
  { key: "task", label: "任务事件" },
];

/** One-line human summary of an event payload for the collapsed row. */
function summarize(ev: SSEvent): string {
  const d = ev.data as Record<string, unknown> | null | undefined;
  if (!d || typeof d !== "object") return "";
  switch (ev.type) {
    case "tool_call":
    case "tool_result":
      return typeof d.tool_name === "string" ? String(d.tool_name) : "";
    case "tool_circuit_open":
      return `工具 ${String(d.tool_name ?? "?")} 熔断，冷却 ${String(
        d.cooldown_sec ?? "?"
      )}s`;
    case "context_compressed":
      return `步骤 #${String(d.step_index ?? "?")} 丢弃 ${String(
        d.dropped ?? "?"
      )} 条早期消息，策略 ${String(d.strategy ?? "?")}`;
    case "step_start":
      return `步骤 #${String(d.index ?? "?")}`;
    case "human_confirm_required":
      return typeof d.tool_name === "string" ? String(d.tool_name) : "";
    case "artifact_created":
      return typeof d.filename === "string" ? String(d.filename) : "";
    case "plan_update":
      return Array.isArray(d.plan) ? `${d.plan.length} 项计划` : "";
    default:
      return "";
  }
}

function fmtTime(ts?: number): string {
  if (!ts || !Number.isFinite(ts)) return "--:--:--";
  const d = new Date(ts * 1000);
  const pad = (n: number, w = 2): string => String(n).padStart(w, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(
    d.getSeconds()
  )}.${pad(d.getMilliseconds(), 3)}`;
}

function pretty(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: "application/x-ndjson" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

interface TraceTabProps {
  taskId: string;
}

interface TraceErrorState {
  status: number;
  message: string;
}

export function TraceTab({ taskId }: TraceTabProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<TraceErrorState | null>(null);
  const [events, setEvents] = useState<SSEvent[]>([]);
  const [raw, setRaw] = useState("");
  const [filter, setFilter] = useState<TraceCategory>("all");
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEvents([]);
    setRaw("");
    setExpanded(new Set());

    getTaskTrace(taskId)
      .then((r) => {
        if (cancelled) return;
        setEvents(r.events);
        setRaw(r.raw);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof TraceFetchError) {
          setError({ status: err.status, message: err.message });
        } else {
          setError({ status: 0, message: err instanceof Error ? err.message : "加载失败" });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [taskId]);

  const filtered = useMemo(
    () =>
      events
        .map((event, index) => ({ event, index }))
        .filter(
          ({ event }) =>
            filter === "all" || TYPE_META[event.type]?.category === filter
        ),
    [events, filter]
  );

  const counts = useMemo(() => {
    const c: Record<TraceCategory, number> = {
      all: events.length,
      tool: 0,
      circuit: 0,
      compress: 0,
      task: 0,
    };
    for (const ev of events) {
      const cat = TYPE_META[ev.type]?.category;
      if (cat) c[cat] += 1;
    }
    return c;
  }, [events]);

  const toggle = (index: number): void => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const onExport = (): void => {
    // `raw` is the verbatim NDJSON text from the backend (see getTaskTrace),
    // so the exported file matches the on-disk trace byte for byte.
    if (!raw) return;
    downloadText(`${taskId}.trace.jsonl`, raw);
  };

  if (loading) {
    return (
      <div className="text-sm text-slate-400 animate-pulse py-8 text-center">
        正在加载 Trace 回放…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded border border-slate-800 bg-slate-900/40 p-6 text-sm">
        {error.status === 404 ? (
          <>
            <div className="font-semibold text-amber-300 mb-1">
              暂无 Trace 回放
            </div>
            <div className="text-slate-400">
              该任务没有可用的运行日志：可能未启用 trace 记录，或日志文件缺失。
              {error.message ? `（${error.message}）` : ""}
            </div>
          </>
        ) : (
          <>
            <div className="font-semibold text-red-300 mb-1">Trace 加载失败</div>
            <div className="text-slate-400">
              {error.message || `HTTP ${error.status}`}
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`px-2.5 py-1 rounded text-xs border transition ${
              filter === f.key
                ? "bg-sky-500/15 border-sky-500/50 text-sky-300"
                : "border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-600"
            }`}
          >
            {f.label}
            <span className="ml-1 opacity-60">{counts[f.key]}</span>
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={onExport}
          disabled={events.length === 0}
          className="px-2.5 py-1 rounded text-xs border border-slate-700 text-slate-300 hover:border-sky-500 hover:text-sky-300 disabled:opacity-40 disabled:hover:border-slate-700 disabled:hover:text-slate-300"
        >
          导出 .jsonl
        </button>
      </div>

      {events.length === 0 ? (
        <div className="text-sm text-slate-500 py-8 text-center">
          暂无 Trace 事件。
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-sm text-slate-500 py-8 text-center">
          当前筛选条件下没有事件。
        </div>
      ) : (
        <div className="space-y-1">
          {filtered.map(({ event, index }) => {
            const meta = TYPE_META[event.type] ?? {
              label: event.type,
              badge: "bg-slate-500/15 text-slate-300 border-slate-500/40",
              category: "task" as TraceCategory,
            };
            const isOpen = expanded.has(index);
            const summary = summarize(event);
            return (
              <div
                key={index}
                className="rounded border border-slate-800 bg-slate-900/40"
              >
                <button
                  onClick={() => toggle(index)}
                  className="w-full text-left flex items-center gap-2 px-2.5 py-1.5 text-xs hover:bg-panelMuted/40"
                >
                  <span className="text-slate-500 w-4 shrink-0">
                    {isOpen ? "▾" : "▸"}
                  </span>
                  <span
                    className={`shrink-0 px-1.5 py-0.5 rounded border text-[11px] font-medium ${meta.badge}`}
                  >
                    {meta.label}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-slate-500">
                    {fmtTime(event.ts)}
                  </span>
                  <span className="flex-1 truncate text-slate-400">
                    {summary}
                  </span>
                </button>
                {isOpen && (
                  <pre className="mx-2.5 mb-2 p-2 bg-slate-950 rounded text-[11px] text-slate-300 overflow-auto max-h-64 font-mono">
                    {pretty(event)}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
