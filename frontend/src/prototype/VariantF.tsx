// PROTOTYPE ONLY. World F「磷光 Phosphor」: the whole console is one amber
// terminal session. ASCII box frames, a tmux-style WATCH pane, blinking block
// cursor, scanlines. Monochrome discipline — red only for alarms.
import { useMemo, useState } from "react";
import type { StepRecord, Task, TaskStatus, ToolCallRecord } from "../types";
import { allTasks, runningMarkers } from "./mock";

export const variantName = "磷光";

const ST_WORD: Record<TaskStatus, string> = {
  PENDING: "QUEUED",
  RUNNING: "RUNNING",
  COMPLETED: "OK",
  FAILED: "FAULT",
  INTERRUPTED: "HALTED",
};

function Hline({ label, tone = "text-ph-dim" }: { label?: string; tone?: string }) {
  return (
    <div className={`flex items-center gap-2 ${tone}`}>
      <span>├─</span>
      {label && <span className="whitespace-nowrap">{label}</span>}
      <span className="flex-1 overflow-hidden whitespace-nowrap tracking-tighter" aria-hidden>
        {"─".repeat(120)}
      </span>
    </div>
  );
}

function ToolLine({ tc }: { tc: ToolCallRecord }) {
  const tag = tc.circuit_open
    ? { t: "⚡ OPEN", c: "text-ph-danger" }
    : tc.status === "success"
    ? { t: tc.need_confirm ? "OK ✓gate" : "OK", c: "text-ph-bright" }
    : tc.status === "failed"
    ? { t: `ERR ${tc.error?.match(/\d{3}/)?.[0] ?? ""}`.trim(), c: "text-ph-danger" }
    : tc.status === "skipped"
    ? { t: "SKIP", c: "text-ph-dim" }
    : { t: "····", c: "text-ph-dim" };
  return (
    <div className="flex">
      <span className="text-ph-dim">│</span>
      <span className="ml-2 text-ph-amber">› {tc.tool_name}</span>
      {(tc.retries ?? 0) > 0 && <span className="ml-1 text-ph-dim">×{tc.retries}</span>}
      <span className="flex-1 mx-2 overflow-hidden whitespace-nowrap text-ph-line" aria-hidden>
        {".".repeat(80)}
      </span>
      <span className={tag.c}>[{tag.t}]</span>
      <span className="ml-2 text-ph-dim">│</span>
    </div>
  );
}

function StepBox({ step, active }: { step: StepRecord; active: boolean }) {
  const state =
    step.status === "done" ? { t: " OK ", c: "text-ph-bright" } : step.status === "running" ? { t: "RUN", c: "text-ph-bright ph-cursor" } : { t: "QUE", c: "text-ph-dim" };
  return (
    <div className={step.status === "pending" ? "opacity-45" : ""}>
      <div className="flex items-center gap-2">
        <span className="text-ph-dim">┌─</span>
        <span className={active ? "text-ph-bright" : "text-ph-amber"}>
          STEP {String(step.index).padStart(2, "0")}
        </span>
        <span className="flex-1 overflow-hidden whitespace-nowrap text-ph-line" aria-hidden>
          {"─".repeat(80)}
        </span>
        <span className={state.c}>[{state.t}]</span>
        <span className="text-ph-dim">─┐</span>
      </div>
      <div className="flex">
        <span className="text-ph-dim">│</span>
        <span className="ml-2 flex-1 text-ph-amber/90">{step.thought || "(queued)"}</span>
        <span className="text-ph-dim">│</span>
      </div>
      {step.tool_calls.map((tc) => (
        <ToolLine key={tc.id} tc={tc} />
      ))}
      <div className="flex items-center gap-2">
        <span className="text-ph-dim">└</span>
        <span className="flex-1 overflow-hidden whitespace-nowrap text-ph-line" aria-hidden>
          {"─".repeat(110)}
        </span>
        <span className="text-ph-dim">┘</span>
      </div>
    </div>
  );
}

function MdTerm({ source }: { source: string }) {
  return (
    <div className="space-y-0.5">
      {source.split("\n").map((line, i) => {
        if (line.startsWith("### ")) return <div key={i} className="text-ph-bright">### {line.slice(4)}</div>;
        if (line.startsWith("## ")) return <div key={i} className="text-ph-bright font-bold">== {line.slice(3)} ==</div>;
        if (line.startsWith("- ")) return <div key={i} className="text-ph-amber/90">  * {line.slice(2).replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1")}</div>;
        const m = line.match(/^(\d+)\.\s+(.*)$/);
        if (m) return <div key={i} className="text-ph-amber/90">  {m[1]}. {m[2].replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1")}</div>;
        return <div key={i} className="text-ph-amber/80">{line.replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1") || " "}</div>;
      })}
    </div>
  );
}

// ---------- watch pane ----------

function WatchPane({ current, onSelect }: { current: string; onSelect: (id: string) => void }) {
  const rows: [string, string, string?][] = [
    ["STATE", "RUNNING", "text-ph-bright"],
    ["STEP", "3 / 4"],
    ["TOOLS", "7 calls"],
    ["RETRY", "3"],
    ["CIRCUIT", "⚡ 1 OPEN", "text-ph-danger"],
    ["CTX", "compressed ×1"],
  ];
  return (
    <aside className="w-72 shrink-0 border-l border-ph-line flex flex-col text-[11px] leading-5">
      <div className="px-3 py-2 border-b border-ph-line text-ph-bright tracking-widest">─ WATCH {"─".repeat(18)}</div>
      <div className="p-3 space-y-0.5 border-b border-ph-line">
        {rows.map(([k, v, c]) => (
          <div key={k} className="flex">
            <span className="text-ph-dim w-16">{k}</span>
            <span className={c ?? "text-ph-amber"}>{v}</span>
          </div>
        ))}
      </div>
      <div className="p-3 border-b border-ph-line">
        <div className="text-ph-dim mb-1">TASKS (click to attach)</div>
        {allTasks.map((t) => (
          <button key={t.id} onClick={() => onSelect(t.id)} className={`block w-full text-left truncate ${current === t.id ? "text-ph-bright" : "text-ph-dim hover:text-ph-amber"}`}>
            {current === t.id ? "▸" : " "} [{t.id.slice(2)}] {ST_WORD[t.status]}
            {current === t.id && " *"}
          </button>
        ))}
      </div>
      <div className="p-3 flex-1 overflow-auto">
        <div className="text-ph-dim mb-1">EVENTS</div>
        {[
          ["14:02:31", "call  write_file"],
          ["14:02:10", "⚡circuit web_search", true],
          ["14:01:58", "err   web_search 429"],
          ["14:01:02", "cmp   dropped 6"],
          ["14:00:11", "step  #3"],
        ].map(([ts, text, alarm], i) => (
          <div key={i} className={`flex gap-2 ${alarm ? "text-ph-danger" : "text-ph-amber/80"}`}>
            <span className="text-ph-dim">{ts as string}</span>
            <span className="truncate">{text as string}</span>
          </div>
        ))}
      </div>
    </aside>
  );
}

// ---------- the world ----------

export function VariantF() {
  const [taskId, setTaskId] = useState(allTasks[0].id);
  const [gateOpen, setGateOpen] = useState(false);
  const task = useMemo(() => allTasks.find((t) => t.id === taskId) ?? allTasks[0], [taskId]);
  const markers = task.id === "t-1024" ? runningMarkers : [];

  return (
    <div className="h-full bg-ph-base text-ph-amber font-mono text-[12.5px] ph-scan">
      <div className="h-full flex flex-col relative z-10">
        {/* session header */}
        <div className="shrink-0 px-4 pt-3 pb-2 border-b border-ph-line">
          <div className="flex items-center gap-2">
            <span className="text-ph-bright tracking-widest">AGENT CONSOLE</span>
            <span className="text-ph-dim">— {task.id} —</span>
            <span className={task.status === "FAILED" ? "text-ph-danger" : "text-ph-bright"}>{ST_WORD[task.status]}</span>
            <span className="flex-1" />
            <button onClick={() => setGateOpen(true)} className="border border-ph-line px-2 py-0.5 text-ph-amber hover:border-ph-amber text-[11px]">
              [gate]
            </button>
            {task.status === "RUNNING" && (
              <button className="border border-ph-danger/60 px-2 py-0.5 text-ph-danger hover:bg-ph-danger/10 text-[11px]">[stop]</button>
            )}
          </div>
        </div>

        <div className="flex-1 flex min-h-0">
          {/* main scrollback */}
          <main className="flex-1 overflow-auto px-4 py-3 space-y-3 min-w-0">
            <div className="text-ph-bright">
              <span className="text-ph-dim">❯</span> {task.user_input}
            </div>
            <div className="text-ph-dim">
              [plan] {task.plan.length} items loaded: {task.plan.map((p) => p.description).join(" → ")}
            </div>
            <Hline label="EXECUTION" />

            {task.steps.map((s) => (
              <div key={s.index} className="space-y-1">
                <StepBox step={s} active={s.status === "running"} />
                {markers
                  .filter((m) => m.type === "context_compressed" && (m.data as { step_index?: number }).step_index === s.index)
                  .map((m, j) => (
                    <div key={`c${j}`} className="text-ph-dim">[sys] context compressed — dropped 6 early messages (drop_oldest)</div>
                  ))}
                {s.tool_calls.some((tc) => tc.circuit_open) &&
                  markers.filter((m) => m.type === "tool_circuit_open").map((m, j) => (
                    <div key={`o${j}`} className="text-ph-danger">[sys] ⚡ CIRCUIT OPEN web_search — cooldown 60s, calls short-circuited</div>
                  ))}
              </div>
            ))}

            {task.status === "RUNNING" && (
              <div className="flex items-center gap-1 text-ph-bright">
                <span className="text-ph-dim">❯</span>
                <span className="ph-cursor">▮</span>
                <span className="text-ph-dim text-[11px]">executing step 03…</span>
              </div>
            )}

            {task.error && <div className="text-ph-danger">[FAULT] {task.error}</div>}

            {task.final_answer && (
              <>
                <Hline label="FINAL ANSWER" />
                <MdTerm source={task.final_answer} />
              </>
            )}

            {task.artifacts.length > 0 && (
              <>
                <Hline label="ARTIFACTS" />
                {task.artifacts.map((a) => (
                  <div key={a.id} className="flex gap-3">
                    <span className="text-ph-bright">{a.filename}</span>
                    <span className="text-ph-dim">{(a.size / 1024).toFixed(1)} KB</span>
                    <span className="text-ph-amber underline cursor-pointer">[preview]</span>
                    <span className="text-ph-amber underline cursor-pointer">[fetch]</span>
                  </div>
                ))}
              </>
            )}
          </main>

          <WatchPane current={taskId} onSelect={setTaskId} />
        </div>

        {/* prompt */}
        <div className="shrink-0 border-t border-ph-line px-4 py-2.5 flex items-center gap-2">
          <span className="text-ph-bright">❯</span>
          <span className="text-ph-dim flex-1">type a task brief…</span>
          <span className="ph-cursor text-ph-bright">▮</span>
          <span className="text-ph-dim text-[11px]">[send ⏎]</span>
        </div>
      </div>

      {/* gate dialog: an ASCII box in the scrollback style */}
      {gateOpen && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/75" onClick={() => setGateOpen(false)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="人工确认"
            className="w-[460px] max-w-[92vw] bg-ph-base border border-ph-amber/50 p-4 font-mono text-[12px]"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === "Escape" && setGateOpen(false)}
            tabIndex={-1}
          >
            <div className="text-ph-bright">┌─ HOLD · HUMAN GATE ─────────────┐</div>
            <div className="my-2 text-ph-amber">
              tool <span className="text-ph-bright">write_file</span> requests WRITE access:
            </div>
            <div className="text-ph-dim"> path: langgraph-selection-report.md</div>
            <div className="text-ph-dim"> size: ~2 KB (overwrite)</div>
            <pre className="mt-2 border border-ph-line p-2 text-[10px] text-ph-dim overflow-auto max-h-28">
              {JSON.stringify({ path: "langgraph-selection-report.md", content: "# LangGraph 1.x 选型报告\n\n(骨架)…" }, null, 2)}
            </pre>
            <div className="mt-3 flex justify-end gap-3">
              <button onClick={() => setGateOpen(false)} className="border border-ph-line px-3 py-1 text-ph-dim hover:text-ph-amber">
                [N] ABORT
              </button>
              <button onClick={() => setGateOpen(false)} className="border border-ph-amber px-3 py-1 text-ph-bright hover:bg-ph-amber/10">
                [Y] APPROVE
              </button>
            </div>
            <div className="mt-2 text-ph-dim text-[10px]">└─ esc to close ────────────────────┘</div>
          </div>
        </div>
      )}
    </div>
  );
}
