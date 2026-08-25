import { useTaskStore } from "../store/taskStore";
import { Task, TraceMarker } from "../types";

/** Render one live circuit-open / compression marker as a flow hint. */
function MarkerRow({ marker }: { marker: TraceMarker }) {
  if (marker.type === "tool_circuit_open") {
    const d = marker.data as { tool_name?: string; cooldown_sec?: number };
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] text-sm bg-red-950/40 border border-red-500/30 text-red-300 rounded-lg px-3 py-2">
          <span className="mr-1">⚡</span>
          工具 {d.tool_name ?? "?"} 熔断，冷却 {d.cooldown_sec ?? "?"}s
        </div>
      </div>
    );
  }
  const d = marker.data as {
    step_index?: number;
    dropped?: number;
    context_tokens?: number;
    strategy?: string;
  };
  const stepPart = d.step_index != null ? `（步骤 #${d.step_index}）` : "";
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] text-sm bg-violet-950/40 border border-violet-500/30 text-violet-300 rounded-lg px-3 py-2">
        <span className="mr-1">🗜</span>
        上下文已压缩：丢弃 {d.dropped ?? "?"} 条早期消息，策略{" "}
        {d.strategy ?? "?"}
        {stepPart}
        {d.context_tokens != null && `（剩余 ${d.context_tokens} tokens）`}
      </div>
    </div>
  );
}

export function MessageStream({ task }: { task: Task }) {
  const markers = useTaskStore((s) => s.markers[task.id] ?? []);

  return (
    <div className="space-y-3">
      {/* User request */}
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-sky-600/90 text-white rounded-lg px-3 py-2 text-sm whitespace-pre-wrap">
          {task.user_input}
        </div>
      </div>

      {/* Plan */}
      {task.plan.length > 0 && (
        <div className="bg-panelMuted/60 rounded-lg p-3 text-sm">
          <div className="text-xs text-slate-400 mb-1">规划</div>
          <ol className="list-decimal list-inside space-y-0.5">
            {task.plan.map((p) => (
              <li key={p.index}>{p.description}</li>
            ))}
          </ol>
        </div>
      )}

      {/* Circuit-open / compression flow markers */}
      {markers.map((m, i) => (
        <MarkerRow key={i} marker={m} />
      ))}

      {/* Final answer */}
      {task.final_answer && (
        <div className="flex justify-start">
          <div className="max-w-[80%] bg-slate-700 rounded-lg px-3 py-2 text-sm whitespace-pre-wrap">
            {task.final_answer}
          </div>
        </div>
      )}

      {task.status === "FAILED" && task.error && (
        <div className="text-sm text-red-400 bg-red-950/40 rounded p-2">
          错误：{task.error}
        </div>
      )}
    </div>
  );
}
