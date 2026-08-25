import { useTaskStore } from "../store/taskStore";
import { Task, ToolCallRecord } from "../types";

function pretty(v: any): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

// P2: render text-heavy tool outputs (git diff/log, MCP text) raw in a <pre>
// instead of JSON-wrapping them, so diffs and logs stay readable.
function renderOutput(tc: ToolCallRecord): React.ReactNode {
  const out = tc.output;
  if (out && typeof out === "object") {
    if (tc.tool_name === "git_diff" && typeof out.diff === "string") {
      return <pre className="bg-slate-900 rounded p-1 overflow-auto max-h-40 whitespace-pre-wrap break-all">{out.diff || "(no diff)"}</pre>;
    }
    if (tc.tool_name === "git_log" && Array.isArray(out.commits)) {
      const text = (out.commits as Array<{ hash?: string; message?: string }>)
        .map((c) => `${c.hash ?? ""} ${c.message ?? ""}`)
        .join("\n");
      return <pre className="bg-slate-900 rounded p-1 overflow-auto max-h-40 whitespace-pre-wrap break-all">{text || "(no commits)"}</pre>;
    }
    if (tc.tool_name.startsWith("mcp__") && typeof out.text === "string" && out.text.length > 0) {
      return <pre className="bg-slate-900 rounded p-1 overflow-auto max-h-40 whitespace-pre-wrap break-all">{out.text}</pre>;
    }
  }
  return <pre className="bg-slate-900 rounded p-1 overflow-auto max-h-40">{pretty(out)}</pre>;
}

function ToolCallBlock({ tc }: { tc: ToolCallRecord }) {
  return (
    <div className="rounded border border-slate-800 p-2 text-xs space-y-1">
      <div className="flex items-center justify-between">
        <span className="font-mono text-sky-300">{tc.tool_name}</span>
        <div className="flex items-center gap-1">
          {tc.circuit_open && (
            <span className="text-[10px] px-1 py-0.5 rounded bg-red-950 border border-red-500/40 text-red-400">
              ⚡熔断
            </span>
          )}
          {(tc.retries ?? 0) > 0 && (
            <span className="text-[10px] px-1 py-0.5 rounded bg-amber-950 border border-amber-500/40 text-amber-400">
              ↻重试×{tc.retries}
            </span>
          )}
          <span className="text-slate-400">{tc.status}</span>
        </div>
      </div>
      {tc.need_confirm && (
        <div className="text-amber-400">需人工确认 {tc.confirmed ? "（已确认）" : "（待确认）"}</div>
      )}
      <div>
        <div className="text-slate-500">入参</div>
        <pre className="bg-slate-900 rounded p-1 overflow-auto max-h-32">
          {pretty(tc.input)}
        </pre>
      </div>
      <div>
        <div className="text-slate-500">出参</div>
        {renderOutput(tc)}
      </div>
      {tc.error && <div className="text-red-400">错误：{tc.error}</div>}
    </div>
  );
}

export function StepDetail({ task }: { task: Task | null }) {
  const selectedStep = useTaskStore((s) => s.selectedStep);
  const events = useTaskStore((s) => s.events);

  const step =
    task && selectedStep != null
      ? task.steps.find((s) => s.index === selectedStep)
      : task && task.steps.length > 0
      ? task.steps[task.steps.length - 1]
      : null;

  return (
    <aside className="w-96 shrink-0 bg-panel border-l border-slate-800 flex flex-col">
      <div className="px-3 py-3 border-b border-slate-800 font-semibold">
        Step 详情
      </div>
      <div className="flex-1 overflow-auto p-3 space-y-3">
        {!task && <div className="text-sm text-slate-500">选择一个任务查看详情。</div>}

        {task && !step && (
          <div className="text-sm text-slate-500">暂无步骤，选择左侧步骤查看入参/出参。</div>
        )}

        {step && (
          <>
            <div className="text-sm">
              <span className="text-slate-400">步骤 #{step.index}</span>
              {step.thought && (
                <div className="mt-1 text-slate-300 whitespace-pre-wrap">
                  {step.thought}
                </div>
              )}
            </div>
            {step.tool_calls.length === 0 ? (
              <div className="text-xs text-slate-500">本步骤未调用工具。</div>
            ) : (
              step.tool_calls.map((tc) => <ToolCallBlock key={tc.id} tc={tc} />)
            )}
          </>
        )}

        <div className="pt-2 border-t border-slate-800">
          <div className="text-xs text-slate-500 mb-1">事件日志（最近 30）</div>
          <div className="space-y-0.5">
            {events
              .slice(-30)
              .reverse()
              .map((e, i) => (
                <div key={i} className="text-[11px] text-slate-400 font-mono">
                  {e.type}
                </div>
              ))}
          </div>
        </div>
      </div>
    </aside>
  );
}
