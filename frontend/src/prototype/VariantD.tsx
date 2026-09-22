// PROTOTYPE ONLY. World D「蓝图 Blueprint」: the agent's execution as a living
// engineering drawing. Grid ground, crop-marked figures, FIG annotations, a
// glowing active node on the execution spine. Signature: the blueprint itself.
import { useMemo, useState } from "react";
import type { StepRecord, Task, TaskStatus, ToolCallRecord } from "../types";
import { allTasks, runningMarkers, timeAgo } from "./mock";

export const variantName = "蓝图";

// ---------- stamps & figure furniture ----------

const STATUS_COLOR: Record<TaskStatus, string> = {
  PENDING: "text-bp-dim border-bp-dim/50",
  RUNNING: "text-bp-signal border-bp-signal/60",
  COMPLETED: "text-bp-ok border-bp-ok/60",
  FAILED: "text-bp-danger border-bp-danger/60",
  INTERRUPTED: "text-bp-warn border-bp-warn/60",
};
const STATUS_LABEL: Record<TaskStatus, string> = {
  PENDING: "QUEUED",
  RUNNING: "RUNNING",
  COMPLETED: "DONE",
  FAILED: "FAULT",
  INTERRUPTED: "HALTED",
};

function Stamp({ status }: { status: TaskStatus }) {
  return (
    <span className={`inline-block border px-1.5 py-0.5 font-mono text-[10px] tracking-widest uppercase ${STATUS_COLOR[status]}`}>
      [ {STATUS_LABEL[status]} ]
    </span>
  );
}

function Fig({ n, title }: { n: string; title: string }) {
  return (
    <div className="font-mono text-[10px] tracking-[0.25em] uppercase text-bp-dim mb-2">
      FIG. {n} — {title}
    </div>
  );
}

/** A panel with blueprint crop-mark corners. */
function BpCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const c = "absolute w-2.5 h-2.5 border-bp-signal/70";
  return (
    <div className={`relative border border-bp-line bg-bp-panel/95 ${className}`}>
      <span className={`${c} top-0 left-0 border-t border-l -translate-x-px -translate-y-px`} />
      <span className={`${c} top-0 right-0 border-t border-r translate-x-px -translate-y-px`} />
      <span className={`${c} bottom-0 left-0 border-b border-l -translate-x-px translate-y-px`} />
      <span className={`${c} bottom-0 right-0 border-b border-r translate-x-px translate-y-px`} />
      {children}
    </div>
  );
}

function pretty(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

// ---------- tool row with dotted leader ----------

function ToolRow({ tc }: { tc: ToolCallRecord }) {
  const tag = tc.circuit_open
    ? { text: "⚡ OPEN", cls: "text-bp-danger" }
    : tc.status === "success"
    ? { text: tc.need_confirm ? "OK ✓GATE" : "OK", cls: "text-bp-ok" }
    : tc.status === "failed"
    ? { text: "FAULT", cls: "text-bp-danger" }
    : tc.status === "skipped"
    ? { text: "SKIP", cls: "text-bp-warn" }
    : { text: "…", cls: "text-bp-dim" };
  return (
    <div className="flex items-baseline font-mono text-[11px]">
      <span className="text-bp-signal">›</span>
      <span className="ml-1.5 text-bp-ink">{tc.tool_name}</span>
      {(tc.retries ?? 0) > 0 && <span className="ml-1 text-bp-warn">×{tc.retries}</span>}
      <span className="flex-1 mx-2 border-b border-dotted border-bp-line translate-y-[-3px]" />
      <span className={tag.cls}>[{tag.text}]</span>
    </div>
  );
}

// ---------- left spine: the execution graph + drawing index ----------

function Spine({
  task,
  selected,
  onSelect,
  currentTask,
  onTask,
}: {
  task: Task;
  selected: number | null;
  onSelect: (i: number) => void;
  currentTask: string;
  onTask: (id: string) => void;
}) {
  return (
    <aside className="w-44 shrink-0 border-r border-bp-line flex flex-col bg-bp-base/80">
      <div className="px-3 pt-3 pb-2 border-b border-bp-line">
        <div className="font-mono text-[10px] tracking-[0.25em] text-bp-dim uppercase">FIG. 01 — GRAPH</div>
      </div>
      <div className="p-3">
        {task.steps.map((s, i) => (
          <div key={s.index} className="flex gap-2.5">
            <div className="flex flex-col items-center">
              <button
                onClick={() => onSelect(s.index)}
                aria-label={`步骤 ${s.index}`}
                className={`w-6 h-6 rounded-full border-2 flex items-center justify-center transition ${
                  s.status === "done"
                    ? "border-bp-ok bg-bp-ok/20"
                    : s.status === "running"
                    ? "border-bp-signal bg-bp-signal/30 bp-glow"
                    : "border-bp-line bg-bp-base"
                } ${selected === s.index ? "ring-1 ring-bp-signal" : ""}`}
              >
                {s.status === "done" && <span className="text-bp-ok text-[10px]">✓</span>}
              </button>
              {i < task.steps.length - 1 && <span className={`w-px h-7 ${s.status === "done" ? "bg-bp-ok/40" : "bg-bp-line"}`} />}
            </div>
            <div className="pt-0.5 min-w-0">
              <div className={`font-display font-bold text-lg leading-none ${s.status === "running" ? "text-bp-signal" : s.status === "done" ? "text-bp-ink" : "text-bp-dim"}`}>
                {String(s.index).padStart(2, "0")}
              </div>
              <div className="font-mono text-[9px] text-bp-dim mt-0.5 uppercase">
                {s.status === "done" ? "drawn" : s.status === "running" ? "drawing" : "queued"}
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-auto border-t border-bp-line p-3">
        <div className="font-mono text-[10px] tracking-[0.25em] text-bp-dim uppercase mb-2">INDEX — TASKS</div>
        {allTasks.map((t) => (
          <button
            key={t.id}
            onClick={() => onTask(t.id)}
            className={`block w-full text-left font-mono text-[11px] py-1 truncate ${
              currentTask === t.id ? "text-bp-signal" : "text-bp-dim hover:text-bp-ink"
            }`}
          >
            {currentTask === t.id ? "▸ " : "  "}
            {t.id} <span className="opacity-60">{STATUS_LABEL[t.status]}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}

// ---------- center flow ----------

function StepBlock({ step, selected, onSelect }: { step: StepRecord; selected: boolean; onSelect: () => void }) {
  return (
    <button onClick={onSelect} className={`w-full text-left flex gap-4 group ${selected ? "" : "opacity-90"}`}>
      <div
        className={`font-display font-bold text-5xl leading-none w-16 text-right shrink-0 transition ${
          step.status === "running" ? "text-bp-signal" : step.status === "done" ? "text-bp-ink/25" : "text-bp-line"
        }`}
      >
        {String(step.index).padStart(2, "0")}
      </div>
      <div className={`flex-1 border-l-2 pl-4 pb-6 ${selected ? "border-bp-signal" : "border-bp-line group-hover:border-bp-dim"}`}>
        <div className="text-sm text-bp-ink">{step.thought || "（排队等待）"}</div>
        {step.tool_calls.length > 0 && (
          <div className="mt-2 space-y-1">
            {step.tool_calls.map((tc) => (
              <ToolRow key={tc.id} tc={tc} />
            ))}
          </div>
        )}
      </div>
    </button>
  );
}

function MdMini({ source }: { source: string }) {
  return (
    <div className="space-y-1.5 text-[13px] leading-relaxed text-bp-dim">
      {source.split("\n").map((line, i) => {
        if (line.startsWith("### ")) return <div key={i} className="font-display font-semibold text-bp-ink text-sm pt-1">{line.slice(4)}</div>;
        if (line.startsWith("## ")) return <div key={i} className="font-display font-semibold text-bp-ink text-base">{line.slice(3)}</div>;
        if (line.startsWith("- ")) return <div key={i} className="flex gap-2"><span className="text-bp-signal">▪</span><span>{line.slice(2)}</span></div>;
        const m = line.match(/^(\d+)\.\s+(.*)$/);
        if (m) return <div key={i} className="flex gap-2"><span className="font-mono text-bp-signal">{m[1]}.</span><span>{m[2]}</span></div>;
        if (!line.trim()) return <div key={i} className="h-1" />;
        return <p key={i}>{line.replace(/\*\*([^*]+)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1")}</p>;
      })}
    </div>
  );
}

// ---------- right inspector ----------

function Inspector({ task, stepIndex }: { task: Task; stepIndex: number | null }) {
  const step = task.steps.find((s) => s.index === stepIndex) ?? task.steps.filter((s) => s.status !== "pending").slice(-1)[0] ?? null;
  return (
    <aside className="w-80 shrink-0 border-l border-bp-line bg-bp-base/80 overflow-auto p-3 space-y-3">
      <div>
        <Fig n="03" title="STEP DETAIL" />
        {step ? (
          <BpCard className="p-3">
            <div className="flex items-baseline justify-between mb-2">
              <span className="font-display font-bold text-2xl text-bp-signal">{String(step.index).padStart(2, "0")}</span>
              <span className="font-mono text-[10px] text-bp-dim uppercase">{step.status}</span>
            </div>
            {step.thought && <p className="text-xs text-bp-dim mb-3">{step.thought}</p>}
            {step.tool_calls.map((tc) => (
              <div key={tc.id} className="mb-2.5">
                <ToolRow tc={tc} />
                <pre className="mt-1 border border-bp-line bg-bp-base p-1.5 font-mono text-[10px] text-bp-dim overflow-auto max-h-24">
                  {tc.error ? `ERR: ${tc.error}` : pretty(tc.output ?? tc.input)}
                </pre>
              </div>
            ))}
            {step.tool_calls.length === 0 && <div className="font-mono text-[10px] text-bp-dim">NO TOOL CALLS</div>}
          </BpCard>
        ) : (
          <div className="font-mono text-[11px] text-bp-dim">NO STEPS YET</div>
        )}
      </div>
      <div>
        <Fig n="04" title="EVENT LOG" />
        <div className="space-y-0.5 font-mono text-[10px] text-bp-dim">
          {[
            ["14:02:31", "tool_call", "write_file"],
            ["14:02:10", "CIRCUIT", "web_search ⚡60s"],
            ["14:01:58", "tool_result", "web_search 429"],
            ["14:01:02", "COMPRESS", "dropped 6"],
            ["14:00:11", "step_start", "#3"],
          ].map(([ts, kind, text], i) => (
            <div key={i} className="flex gap-2">
              <span className="opacity-60">{ts}</span>
              <span className={kind === "CIRCUIT" ? "text-bp-danger" : kind === "COMPRESS" ? "text-bp-warn" : ""}>{kind}</span>
              <span className="truncate">{text}</span>
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}

// ---------- the world ----------

export function VariantD() {
  const [taskId, setTaskId] = useState(allTasks[0].id);
  const [selectedStep, setSelectedStep] = useState<number | null>(3);
  const [gateOpen, setGateOpen] = useState(false);
  const task = useMemo(() => allTasks.find((t) => t.id === taskId) ?? allTasks[0], [taskId]);
  const markers = task.id === "t-1024" ? runningMarkers : [];
  const toolCount = task.steps.reduce((n, s) => n + s.tool_calls.length, 0);

  return (
    <div className="h-full flex flex-col bg-bp-base text-bp-ink bp-grid">
      {/* title block strip */}
      <header className="h-12 shrink-0 border-b border-bp-line bg-bp-base/90 flex items-center gap-3 px-4">
        <span className="font-mono text-[10px] tracking-[0.3em] text-bp-signal uppercase">▲ Agent·Blueprint</span>
        <span className="text-bp-line">|</span>
        <span className="font-display font-semibold truncate">{task.title || "未命名任务"}</span>
        <span className="font-mono text-[10px] text-bp-dim hidden md:inline">
          {task.id} · {timeAgo(task.updated_at)} · {toolCount} CALLS
        </span>
        <span className="flex-1" />
        <button onClick={() => setGateOpen(true)} className="font-mono text-[10px] tracking-widest border border-bp-warn/60 text-bp-warn px-2 py-1 hover:bg-bp-warn/10">
          [ GATE ]
        </button>
        {task.status === "RUNNING" && (
          <button className="font-mono text-[10px] tracking-widest border border-bp-danger/60 text-bp-danger px-2 py-1 hover:bg-bp-danger/10">
            [ STOP ]
          </button>
        )}
        <Stamp status={task.status} />
      </header>

      <div className="flex-1 flex min-h-0">
        <Spine task={task} selected={selectedStep} onSelect={setSelectedStep} currentTask={taskId} onTask={(id) => { setTaskId(id); setSelectedStep(null); }} />

        <main className="flex-1 overflow-auto px-6 py-5 space-y-5 min-w-0">
          {/* brief */}
          <section>
            <Fig n="00" title="BRIEF" />
            <div className="border-l-2 border-bp-signal pl-4 text-sm text-bp-ink">{task.user_input}</div>
          </section>

          {/* plan */}
          {task.plan.length > 0 && (
            <section>
              <Fig n="02" title="PLAN" />
              <BpCard className="p-3">
                {task.plan.map((p) => (
                  <div key={p.index} className="flex items-baseline font-mono text-[11px] py-0.5">
                    <span className={p.status === "done" ? "text-bp-ok" : p.status === "active" ? "text-bp-signal" : "text-bp-dim"}>
                      {String(p.index).padStart(2, "0")}
                    </span>
                    <span className="flex-1 mx-2 border-b border-dotted border-bp-line translate-y-[-3px]" />
                    <span className={p.status === "pending" ? "text-bp-dim" : "text-bp-ink"}>{p.description}</span>
                  </div>
                ))}
              </BpCard>
            </section>
          )}

          {/* steps with inline annotations */}
          <section className="space-y-0">
            {task.steps.map((s) => (
              <div key={s.index}>
                <StepBlock step={s} selected={selectedStep === s.index} onSelect={() => setSelectedStep(s.index)} />
                {markers
                  .filter((m) => m.type === "context_compressed" && (m.data as { step_index?: number }).step_index === s.index)
                  .map((m, j) => (
                    <div key={`c${j}`} className="ml-20 mb-4 font-mono text-[10px] text-bp-warn">
                      NOTE — context compressed: dropped 6 early messages (strategy drop_oldest)
                    </div>
                  ))}
                {s.tool_calls.some((tc) => tc.circuit_open) &&
                  markers.filter((m) => m.type === "tool_circuit_open").map((m, j) => (
                    <div key={`o${j}`} className="ml-20 mb-4 font-mono text-[10px] text-bp-danger">
                      NOTE — CIRCUIT OPEN on web_search, cooldown 60s, calls short-circuited
                    </div>
                  ))}
              </div>
            ))}
          </section>

          {task.error && (
            <div className="font-mono text-[11px] text-bp-danger border border-bp-danger/50 p-2.5">FAULT — {task.error}</div>
          )}
          {task.final_answer && (
            <section>
              <Fig n="05" title="ANSWER" />
              <BpCard className="p-4">
                <MdMini source={task.final_answer} />
              </BpCard>
            </section>
          )}
          {task.artifacts.length > 0 && (
            <section>
              <Fig n="06" title="ARTIFACTS" />
              {task.artifacts.map((a) => (
                <div key={a.id} className="flex items-baseline font-mono text-[11px] py-0.5">
                  <span className="text-bp-ink">{a.filename}</span>
                  <span className="flex-1 mx-2 border-b border-dotted border-bp-line translate-y-[-3px]" />
                  <span className="text-bp-dim">{(a.size / 1024).toFixed(1)} KB</span>
                  <span className="ml-3 text-bp-signal cursor-pointer hover:underline">PREVIEW</span>
                  <span className="ml-2 text-bp-ok cursor-pointer hover:underline">FETCH</span>
                </div>
              ))}
            </section>
          )}
        </main>

        <Inspector task={task} stepIndex={selectedStep} />
      </div>

      {/* brief input */}
      <footer className="shrink-0 border-t border-bp-line bg-bp-base/90 px-4 py-2.5 flex items-center gap-3 font-mono text-[12px]">
        <span className="text-bp-signal tracking-widest">BRIEF ▸</span>
        <span className="flex-1 text-bp-dim">描述下一个任务…</span>
        <span className="border border-bp-signal/60 text-bp-signal px-2.5 py-1 text-[10px] tracking-widest cursor-pointer hover:bg-bp-signal/10">[ TRANSMIT ]</span>
      </footer>

      {/* human gate dialog */}
      {gateOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70" onClick={() => setGateOpen(false)}>
          <div className="w-[440px] max-w-[92vw]" onClick={(e) => e.stopPropagation()}>
            <BpCard className="p-5">
              <div className="font-mono text-[10px] tracking-[0.25em] text-bp-warn uppercase mb-2">HOLD — HUMAN GATE</div>
              <p className="text-sm text-bp-ink mb-1">
                工具 <span className="font-mono text-bp-signal">write_file</span> 将覆写工作区文件：
              </p>
              <p className="font-mono text-[12px] text-bp-dim mb-3">langgraph-selection-report.md (~2 KB)</p>
              <pre className="border border-bp-line bg-bp-base p-2 font-mono text-[10px] text-bp-dim overflow-auto max-h-32 mb-4">
                {JSON.stringify({ path: "langgraph-selection-report.md", content: "# LangGraph 1.x 选型报告\n\n(骨架)…" }, null, 2)}
              </pre>
              <div className="flex justify-end gap-2 font-mono text-[11px] tracking-widest">
                <button onClick={() => setGateOpen(false)} className="border border-bp-line text-bp-dim px-3 py-1.5 hover:text-bp-ink">[ ABORT ]</button>
                <button onClick={() => setGateOpen(false)} className="border border-bp-ok/60 text-bp-ok px-3 py-1.5 hover:bg-bp-ok/10">[ APPROVE ]</button>
              </div>
            </BpCard>
          </div>
        </div>
      )}
    </div>
  );
}
