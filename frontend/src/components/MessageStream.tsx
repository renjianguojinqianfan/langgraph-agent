import { useTaskStore } from "../store/taskStore";
import { compressFaceParts } from "../types";
import { ChatTurn, Task, TraceMarker } from "../types";

/** Render one live compression marker as a flow hint. */
function MarkerRow({ marker }: { marker: TraceMarker }) {
  const d = marker.data as {
    step_index?: number;
    dropped?: number;
    band_evicted?: number;
    context_tokens?: number;
    strategy?: string;
  };
  const stepPart = d.step_index != null ? `（步骤 #${d.step_index}）` : "";
  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] text-sm bg-violet-950/40 border border-violet-500/30 text-violet-300 rounded-lg px-3 py-2">
        <span className="mr-1">🗜</span>
        上下文已压缩：{compressFaceParts(d)}，策略{" "}
        {d.strategy ?? "?"}
        {stepPart}
        {d.context_tokens != null && `（剩余 ${d.context_tokens} tokens）`}
      </div>
    </div>
  );
}

function Turn({ turn }: { turn: ChatTurn }) {
  const mine = turn.role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
          mine ? "bg-sky-600/90 text-white" : "bg-slate-700 text-slate-100"
        }`}
      >
        {turn.content}
      </div>
    </div>
  );
}

/**
 * The conversation face (#139): one task = one multi-turn conversation.
 *
 * Turns come from `Task.conversation` (persisted by the backend at the end of
 * each round); a pre-S5 task without the field falls back to its first input.
 * The plan has its own area (`PlanPanel`) and tool traffic its own timeline
 * (`StepTimeline`), so neither is duplicated here.
 */
export function MessageStream({ task }: { task: Task }) {
  const markers = useTaskStore((s) => s.markers[task.id] ?? []);
  const conversation: ChatTurn[] = task.conversation.length
    ? task.conversation
    : task.user_input
      ? [{ role: "user", content: task.user_input }]
      : [];

  return (
    <div className="space-y-3">
      {conversation.map((turn, i) => (
        <Turn key={i} turn={turn} />
      ))}

      {/* Compression flow markers */}
      {markers.map((m, i) => (
        <MarkerRow key={i} marker={m} />
      ))}

      {task.status === "FAILED" && task.error && (
        <div className="text-sm text-red-400 bg-red-950/40 rounded p-2">
          错误：{task.error}
        </div>
      )}
    </div>
  );
}
