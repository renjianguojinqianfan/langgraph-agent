// PROTOTYPE ONLY. World E「工作室 Atelier」: the console as a craftsperson's
// notebook. Zero cards — grouping by hairlines and whitespace; Fraunces serif
// display, marginalia step numerals, diamonds for status. Signature: typography.
import { useMemo, useState } from "react";
import type { StepRecord, Task, TaskStatus, ToolCallRecord } from "../types";
import { allTasks, runningMarkers, timeAgo } from "./mock";

export const variantName = "工作室";

const DIAMOND: Record<TaskStatus, string> = {
  PENDING: "text-ed-dim",
  RUNNING: "text-ed-accent",
  COMPLETED: "text-ed-ok",
  FAILED: "text-ed-danger",
  INTERRUPTED: "text-ed-warn",
};
const WORD: Record<TaskStatus, string> = {
  PENDING: "等待",
  RUNNING: "进行中",
  COMPLETED: "完成",
  FAILED: "失败",
  INTERRUPTED: "已停",
};

function Hr() {
  return <div className="border-t border-ed-line" />;
}

function ToolLine({ tc }: { tc: ToolCallRecord }) {
  const note = tc.circuit_open
    ? { s: "熔断，冷却中", c: "text-ed-danger" }
    : tc.status === "success"
    ? { s: tc.need_confirm ? "成功 · 已经确认闸" : "成功", c: "text-ed-ok" }
    : tc.status === "failed"
    ? { s: `失败${(tc.retries ?? 0) > 0 ? ` · 重试 ${tc.retries} 次` : ""}`, c: "text-ed-danger" }
    : tc.status === "skipped"
    ? { s: "跳过", c: "text-ed-warn" }
    : { s: "待执行", c: "text-ed-dim" };
  return (
    <div className="flex items-baseline gap-2 font-mono text-[11px]">
      <span className="text-ed-dim">{tc.tool_name}</span>
      <span className={`ml-auto ${note.c}`}>{note.s}</span>
    </div>
  );
}

function StepRow({ step }: { step: StepRecord }) {
  const [open, setOpen] = useState(false);
  const pending = step.status === "pending";
  return (
    <div className="flex gap-5">
      {/* marginalia numeral */}
      <div className={`w-14 shrink-0 text-right font-serif italic text-xl leading-7 ${pending ? "text-ed-line" : step.status === "running" ? "text-ed-accent" : "text-ed-dim"}`}>
        №{String(step.index).padStart(2, "0")}
      </div>
      <div className={`flex-1 pb-7 ${pending ? "opacity-50" : ""}`}>
        <button onClick={() => setOpen((v) => !v)} className="text-left w-full group">
          <span className={`text-[15px] leading-7 ${pending ? "text-ed-dim" : "text-ed-ink"}`}>
            {step.thought || "（排队等待）"}
          </span>
          {step.status === "running" && <span className="ml-2 font-serif italic text-sm text-ed-accent">进行中…</span>}
          {step.tool_calls.length > 0 && (
            <span className="ml-2 text-xs text-ed-dim group-hover:text-ed-accent transition">{open ? "收起 ↑" : "明细 ↓"}</span>
          )}
        </button>
        {step.tool_calls.length > 0 && (
          <div className="mt-1.5 space-y-0.5 max-w-md">
            {step.tool_calls.map((tc) => (
              <ToolLine key={tc.id} tc={tc} />
            ))}
          </div>
        )}
        {open && step.tool_calls.length > 0 && (
          <div className="mt-3 space-y-2 max-w-lg">
            {step.tool_calls.map((tc) => (
              <pre key={tc.id} className="font-mono text-[10px] leading-relaxed text-ed-dim border-l border-ed-line pl-3 overflow-auto max-h-24 whitespace-pre-wrap">
                {tc.tool_name}({JSON.stringify(tc.input)}){tc.error ? `\n→ 错误:${tc.error}` : `\n→ ${JSON.stringify(tc.output)}`}
              </pre>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MdSerif({ source }: { source: string }) {
  return (
    <div className="space-y-2 text-[15px] leading-7 text-ed-ink/90">
      {source.split("\n").map((line, i) => {
        if (line.startsWith("### ")) return <div key={i} className="font-serif text-lg text-ed-ink pt-2">{line.slice(4)}</div>;
        if (line.startsWith("## ")) return <div key={i} className="font-serif text-2xl text-ed-ink pt-1">{line.slice(3)}</div>;
        if (line.startsWith("- ")) {
          return (
            <div key={i} className="flex gap-2.5">
              <span className="text-ed-accent">◆</span>
              <span>{renderInline(line.slice(2))}</span>
            </div>
          );
        }
        const m = line.match(/^(\d+)\.\s+(.*)$/);
        if (m) {
          return (
            <div key={i} className="flex gap-2.5">
              <span className="font-serif italic text-ed-accent">{m[1]}.</span>
              <span>{renderInline(m[2])}</span>
            </div>
          );
        }
        if (!line.trim()) return <div key={i} className="h-1" />;
        return <p key={i}>{renderInline(line)}</p>;
      })}
    </div>
  );
}

function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) => {
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={i} className="text-ed-ink font-medium">{p.slice(2, -2)}</strong>;
    if (p.startsWith("`") && p.endsWith("`")) return <code key={i} className="font-mono text-[0.82em] text-ed-accent">{p.slice(1, -1)}</code>;
    return <span key={i}>{p}</span>;
  });
}

export function VariantE() {
  const [taskId, setTaskId] = useState(allTasks[0].id);
  const [gateOpen, setGateOpen] = useState(false);
  const task = useMemo(() => allTasks.find((t) => t.id === taskId) ?? allTasks[0], [taskId]);
  const markers = task.id === "t-1024" ? runningMarkers : [];
  const toolCount = task.steps.reduce((n, s) => n + s.tool_calls.length, 0);

  return (
    <div className="h-full flex bg-ed-base text-ed-ink">
      {/* left rail: quiet task index */}
      <aside className="w-52 shrink-0 border-r border-ed-line flex flex-col">
        <div className="px-5 pt-6 pb-4">
          <div className="font-serif italic text-lg text-ed-ink">任务集</div>
          <div className="text-[11px] text-ed-dim mt-0.5">{allTasks.length} 项记录</div>
        </div>
        <Hr />
        <div className="flex-1 overflow-auto py-2">
          {allTasks.map((t) => (
            <button
              key={t.id}
              onClick={() => setTaskId(t.id)}
              className={`w-full text-left px-5 py-2.5 transition ${
                taskId === t.id ? "bg-ed-raised" : "hover:bg-ed-raised/50"
              }`}
            >
              <div className={`text-[13px] leading-5 truncate ${taskId === t.id ? "text-ed-ink font-serif italic" : "text-ed-ink/85"}`}>
                {t.title || t.user_input}
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-[10px] text-ed-dim">
                <span className={DIAMOND[t.status]}>◆</span>
                <span>{WORD[t.status]}</span>
                <span>·</span>
                <span>{timeAgo(t.updated_at)}</span>
              </div>
            </button>
          ))}
        </div>
        <Hr />
        <div className="px-5 py-3.5">
          <span className="text-[12px] text-ed-dim underline underline-offset-4 decoration-ed-line hover:text-ed-accent cursor-pointer transition">
            + 开始新任务
          </span>
        </div>
      </aside>

      {/* the notebook column */}
      <main className="flex-1 overflow-auto">
        <div className="max-w-[660px] mx-auto px-8 pt-12 pb-16">
          {/* head */}
          <div className="flex items-baseline justify-between">
            <div className="font-serif italic text-ed-accent tracking-wide text-[15px]">
              Nº {task.id.replace("t-", "")} — {task.status === "RUNNING" ? "en cours" : task.status === "COMPLETED" ? "terminé" : task.status === "FAILED" ? "en échec" : "arrêté"}
            </div>
            <button onClick={() => setGateOpen(true)} className="text-[11px] text-ed-dim underline underline-offset-4 decoration-ed-line hover:text-ed-accent transition">
              预览确认弹窗
            </button>
          </div>
          <h1 className="font-serif text-[34px] leading-snug mt-2 text-ed-ink">{task.title || "未命名任务"}</h1>
          <div className="mt-2 text-[12px] text-ed-dim">
            {timeAgo(task.created_at)}开始 · {toolCount} 次工具调用
            {markers.some((m) => m.type === "tool_circuit_open") && " · 1 次熔断"}
          </div>

          <div className="my-7"><Hr /></div>

          {/* brief as a pull-quote */}
          <p className="font-serif italic text-[19px] leading-8 text-ed-ink/90">“{task.user_input}”</p>

          <div className="my-7"><Hr /></div>

          {/* plan */}
          {task.plan.length > 0 && (
            <>
              <div className="space-y-1.5">
                {task.plan.map((p) => (
                  <div key={p.index} className="flex items-baseline gap-3">
                    <span className={`font-serif italic text-[15px] w-8 text-right ${p.status === "active" ? "text-ed-accent" : "text-ed-dim"}`}>
                      {p.index}.
                    </span>
                    <span className={`text-[14px] ${p.status === "done" ? "text-ed-dim line-through decoration-ed-line" : p.status === "pending" ? "text-ed-dim" : "text-ed-ink"}`}>
                      {p.description}
                    </span>
                    {p.status === "done" && <span className="text-ed-ok text-xs">✓</span>}
                  </div>
                ))}
              </div>
              <div className="my-7"><Hr /></div>
            </>
          )}

          {/* steps */}
          <div>
            {task.steps.map((s) => (
              <div key={s.index}>
                <StepRow step={s} />
                {markers
                  .filter((m) => m.type === "context_compressed" && (m.data as { step_index?: number }).step_index === s.index)
                  .map((m, j) => (
                    <div key={`c${j}`} className="flex gap-5 -mt-4 pb-7">
                      <span className="w-14 shrink-0" />
                      <span className="font-serif italic text-[13px] text-ed-dim">— 上下文在此压缩，六条早期消息让位于新知 —</span>
                    </div>
                  ))}
                {s.tool_calls.some((tc) => tc.circuit_open) &&
                  markers.filter((m) => m.type === "tool_circuit_open").map((m, j) => (
                    <div key={`o${j}`} className="flex gap-5 -mt-4 pb-7">
                      <span className="w-14 shrink-0" />
                      <span className="font-serif italic text-[13px] text-ed-danger">— web_search 熔断，六十秒内不再叩门 —</span>
                    </div>
                  ))}
              </div>
            ))}
          </div>

          {task.error && (
            <>
              <Hr />
              <p className="my-6 font-serif italic text-[15px] text-ed-danger">任务于此止步:{task.error}</p>
            </>
          )}

          {task.final_answer && (
            <>
              <div className="my-2"><Hr /></div>
              <div className="font-serif italic text-ed-accent text-[15px] mt-7 mb-4">最终回答</div>
              <MdSerif source={task.final_answer} />
            </>
          )}

          {task.artifacts.length > 0 && (
            <>
              <div className="my-7"><Hr /></div>
              <div className="space-y-1.5">
                {task.artifacts.map((a) => (
                  <div key={a.id} className="flex items-baseline gap-2 text-[13px]">
                    <span className="font-mono text-[12px] text-ed-ink">{a.filename}</span>
                    <span className="text-ed-dim text-[11px]">{(a.size / 1024).toFixed(1)} KB</span>
                    <span className="ml-auto text-[12px] text-ed-dim underline underline-offset-4 decoration-ed-line hover:text-ed-accent cursor-pointer transition">预览</span>
                    <span className="text-[12px] text-ed-dim underline underline-offset-4 decoration-ed-line hover:text-ed-accent cursor-pointer transition">下载</span>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* input: a bare ruled line */}
          <div className="mt-12 flex items-baseline gap-3 border-b border-ed-line pb-2">
            <span className="font-serif italic text-ed-dim flex-1 text-[15px]">再交代一件事…</span>
            <span className="text-[12px] text-ed-dim underline underline-offset-4 decoration-ed-line hover:text-ed-accent cursor-pointer transition">发送 →</span>
          </div>
          <div className="mt-1.5 text-[10px] text-ed-dim/70">Ctrl/⌘ + Enter</div>
        </div>
      </main>

      {/* gate dialog: warm paper card */}
      {gateOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setGateOpen(false)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="ed-gate-title"
            className="w-[420px] max-w-[92vw] bg-ed-raised border border-ed-line p-7"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === "Escape" && setGateOpen(false)}
            tabIndex={-1}
          >
            <div id="ed-gate-title" className="font-serif text-xl text-ed-ink">需要您的确认</div>
            <p className="mt-3 text-[14px] leading-6 text-ed-ink/85">
              工具 <span className="font-mono text-[12px] text-ed-accent">write_file</span> 打算覆写工作区中的{" "}
              <span className="font-mono text-[12px]">langgraph-selection-report.md</span>(约 2 KB)。
            </p>
            <p className="mt-1 font-serif italic text-[13px] text-ed-dim">落笔之前，请确认这笔该由它来写。</p>
            <div className="my-5"><Hr /></div>
            <div className="flex justify-end gap-5 text-[13px]">
              <button onClick={() => setGateOpen(false)} className="text-ed-dim underline underline-offset-4 decoration-ed-line hover:text-ed-ink transition">
                拒绝
              </button>
              <button onClick={() => setGateOpen(false)} className="text-ed-accent font-medium underline underline-offset-4 decoration-ed-accent/50 hover:decoration-ed-accent transition">
                允许执行
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
