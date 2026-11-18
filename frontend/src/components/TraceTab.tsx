import { useEffect, useMemo, useState } from "react";
import { compressFaceParts } from "../types";
import { getTaskTrace, TraceFetchError } from "../api/client";
import { SSEvent } from "../types";
import {
  callCompleteness,
  collectModelCalls,
  modelCallSummary,
  shortCallId,
  type ModelCall,
  type ModelCallRecord,
} from "../lib/modelCalls";

type TraceCategory = "all" | "tool" | "compress" | "task" | "model";

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
  task_resumed: {
    label: "续跑/续说",
    badge: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
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
  context_compressed: {
    label: "上下文压缩",
    badge: "bg-violet-500/15 text-violet-300 border-violet-500/40",
    category: "compress",
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
  task_failed: {
    label: "失败",
    badge: "bg-red-500/15 text-red-300 border-red-500/40",
    category: "task",
  },
  task_interrupted: {
    label: "停下",
    badge: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    category: "task",
  },
  llm_call: {
    label: "模型调用",
    badge: "bg-indigo-500/15 text-indigo-300 border-indigo-500/40",
    category: "model",
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

// #83: 五档确认终态文案表随确认闸门（#138）退役，summarize 不再渲染 confirm 事件。

const FILTERS: { key: TraceCategory; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "model", label: "模型" },
  { key: "tool", label: "工具" },
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
    case "context_compressed":
      return `步骤 #${String(d.step_index ?? "?")} ${compressFaceParts(d)}，策略 ${String(
        d.strategy ?? "?"
      )}`;
    case "step_start":
      return `步骤 #${String(d.index ?? "?")}`;
    default:
      return "";
  }
}

function fmtTime(ts?: number | null): string {
  if (!ts || !Number.isFinite(ts)) return "--:--:--";
  const d = new Date(ts * 1000);
  const pad = (n: number, w = 2): string => String(n).padStart(w, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(
    d.getSeconds()
  )}.${pad(d.getMilliseconds(), 3)}`;
}

function pretty(v: unknown): string {
  try {
    const text = JSON.stringify(v, null, 2);
    return text === undefined ? String(v) : text;
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

function completeness(
  call: ModelCall
): { label: string; className: string } {
  const state = callCompleteness(call);
  if (state === "pending") {
    return {
      label: "未完成",
      className: "border-amber-500/40 text-amber-300",
    };
  }
  if (state === "truncated") {
    return {
      label: "请求预览已截断",
      className: "border-amber-500/40 text-amber-300",
    };
  }
  return { label: "记录完整", className: "border-slate-700 text-tertiary" };
}

function statusBadge(status: string): string {
  if (status === "ok") return "text-emerald-300";
  if (status === "error") return "text-red-300";
  if (status === "aborted") return "text-amber-300";
  return "text-tertiary";
}

function AttemptDetail({ record }: { record: ModelCallRecord }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-950/60 p-2 space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px]">
        <span className="text-slate-300">attempt {record.attempt}</span>
        <span className={statusBadge(record.status)}>{record.status}</span>
        <span className="text-tertiary">
          {fmtTime(record.started_at)} → {fmtTime(record.ended_at)}
        </span>
        <span className="text-tertiary">{record.protocol || "?"}</span>
        {record.pending && (
          <span className="text-amber-300">
            这次调用未完成（进程中断，或还没有响应）
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-tertiary">
        <span>归属 task: {record.owner_task_id || "?"}</span>
        <span>用途: {record.source || "?"}</span>
        <span>round: {record.round ?? "?"}</span>
        <span>step: {record.step ?? "?"}</span>
        <span>模型: {record.model || "?"}</span>
      </div>
      {record.truncated && (
        <p className="text-[11px] text-amber-300">
          请求预览超过 128 KiB，trace 只保留了前缀；记录本身没有再截断，完整文件可用上方导出读取。
        </p>
      )}
      <div>
        <div className="text-[11px] text-tertiary mb-1">请求</div>
        <pre className="p-2 bg-slate-950 rounded text-[11px] text-slate-300 overflow-auto max-h-64 font-mono">
          {pretty(record.request)}
        </pre>
      </div>
      <div>
        <div className="text-[11px] text-tertiary mb-1">响应</div>
        <pre className="p-2 bg-slate-950 rounded text-[11px] text-slate-300 overflow-auto max-h-64 font-mono">
          {pretty(record.response)}
        </pre>
      </div>
      <div>
        <div className="text-[11px] text-tertiary mb-1">错误</div>
        <pre className="p-2 bg-slate-950 rounded text-[11px] text-slate-300 overflow-auto max-h-40 font-mono">
          {record.error || "（无）"}
        </pre>
      </div>
      <div>
        <div className="text-[11px] text-tertiary mb-1">usage</div>
        <pre className="p-2 bg-slate-950 rounded text-[11px] text-slate-300 overflow-auto max-h-40 font-mono">
          {pretty(record.usage)}
        </pre>
      </div>
    </div>
  );
}

function ModelCallRow({ taskId, call }: { taskId: string; call: ModelCall }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<ModelCallRecord[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const mark = completeness(call);
  const records = loaded ?? call.records;

  useEffect(() => {
    if (!open || loaded !== null) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    // Read only this call on demand; the full trace stays the export/replay
    // path. A 413 (trace larger than the parsing bound) falls back to the
    // records the replay face already loaded.
    getTaskTrace(taskId, { type: "llm_call", call_id: call.call_id })
      .then((result) => {
        if (cancelled) return;
        const detail = collectModelCalls(result.events).find(
          (item) => item.call_id === call.call_id
        );
        setLoaded(detail?.records.length ? detail.records : call.records);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof TraceFetchError
            ? err.message
            : err instanceof Error
              ? err.message
              : "读取失败"
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, loaded, taskId, call.call_id, call.records]);

  return (
    <div className="rounded border border-slate-800 bg-slate-900/40">
      <button
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="w-full text-left flex items-center gap-2 px-2.5 py-1.5 text-xs hover:bg-surface-hover"
      >
        <span className="text-tertiary w-4 shrink-0">{open ? "▾" : "▸"}</span>
        <span className="shrink-0 px-1.5 py-0.5 rounded border text-[11px] font-medium bg-indigo-500/15 text-indigo-300 border-indigo-500/40">
          模型调用
        </span>
        <span className="shrink-0 font-mono text-[11px] text-tertiary">
          {fmtTime(call.latest.started_at)}
        </span>
        <span className="min-w-0 flex-1 truncate text-slate-400">
          {modelCallSummary(call)}
        </span>
        <span className="shrink-0 font-mono text-[11px] text-tertiary">
          {shortCallId(call.call_id)}
        </span>
        <span
          className={`shrink-0 px-1.5 py-0.5 rounded border text-[11px] ${mark.className}`}
        >
          {mark.label}
        </span>
      </button>
      {open && (
        <div className="mx-2.5 mb-2 space-y-2">
          <div className="font-mono text-[11px] text-tertiary break-all">
            call_id: {call.call_id} · attempt: {call.records.length}
          </div>
          {loading && (
            <div className="text-[11px] text-slate-400 animate-pulse">
              正在读取调用记录…
            </div>
          )}
          {error && (
            <div className="text-[11px] text-amber-300">
              增量读取失败（{error}）；下面显示回放区已加载的记录。
            </div>
          )}
          {records.map((record) => (
            <AttemptDetail
              key={`${record.call_id}:${record.attempt}`}
              record={record}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ModelCallList({
  taskId,
  calls,
}: {
  taskId: string;
  calls: readonly ModelCall[];
}) {
  if (calls.length === 0) {
    return (
      <div className="text-sm text-tertiary py-6 text-center">
        暂无模型调用记录。
      </div>
    );
  }
  return (
    <div className="space-y-1">
      {calls.map((call) => (
        <ModelCallRow key={call.call_id} taskId={taskId} call={call} />
      ))}
    </div>
  );
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

  // The list is derived from the replay snapshot already loaded for this
  // face. A second `?type=llm_call` request would re-read the same full file
  // just to build the same list; the incremental API is used where it pays —
  // the per-call detail on expand (`call_id`) and the export stays the
  // unfiltered raw trace.
  const calls = useMemo(() => collectModelCalls(events), [events]);

  const filtered = useMemo(
    () =>
      events
        .map((event, index) => ({ event, index }))
        .filter(
          ({ event }) =>
            event.type !== "llm_call" &&
            (filter === "all" || TYPE_META[event.type]?.category === filter)
        ),
    [events, filter]
  );

  const counts = useMemo(() => {
    const c: Record<TraceCategory, number> = {
      all: 0,
      model: calls.length,
      tool: 0,
      compress: 0,
      task: 0,
    };
    for (const ev of events) {
      const cat = TYPE_META[ev.type]?.category;
      if (cat && cat !== "model") {
        c[cat] += 1;
        c.all += 1;
      }
    }
    c.all += calls.length;
    return c;
  }, [events, calls.length]);

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

  const showCalls = filter === "all" || filter === "model";
  const showEvents = filter !== "model";
  const visible = (showCalls ? calls.length : 0) + (showEvents ? filtered.length : 0);

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
        <div className="text-sm text-tertiary py-8 text-center">
          暂无 Trace 事件。
        </div>
      ) : visible === 0 ? (
        <div className="text-sm text-tertiary py-8 text-center">
          {filter === "model"
            ? "暂无模型调用记录。"
            : "当前筛选条件下没有事件。"}
        </div>
      ) : (
        <div className="space-y-3">
          {showCalls && (
            <section className="space-y-1" aria-label="模型调用">
              {filter === "all" && calls.length > 0 && (
                <div className="px-1 font-mono text-[11px] text-tertiary">
                  模型调用
                </div>
              )}
              <ModelCallList taskId={taskId} calls={calls} />
            </section>
          )}
          {showEvents && (
            <section className="space-y-1" aria-label="运行事件">
              {filter === "all" && filtered.length > 0 && (
                <div className="px-1 font-mono text-[11px] text-tertiary">
                  运行事件
                </div>
              )}
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
                      className="w-full text-left flex items-center gap-2 px-2.5 py-1.5 text-xs hover:bg-surface-hover"
                    >
                      <span className="text-tertiary w-4 shrink-0">
                        {isOpen ? "▾" : "▸"}
                      </span>
                      <span
                        className={`shrink-0 px-1.5 py-0.5 rounded border text-[11px] font-medium ${meta.badge}`}
                      >
                        {meta.label}
                      </span>
                      <span className="shrink-0 font-mono text-[11px] text-tertiary">
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
            </section>
          )}
        </div>
      )}
    </div>
  );
}
