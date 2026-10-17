import { useEffect, useState } from "react";
import { getTask, putPlan } from "../api/client";
import { useTaskStore } from "../store/taskStore";
import { Task } from "../types";

interface DraftStep {
  description: string;
  status: string;
}

const STATUS_LABEL: Record<string, string> = {
  pending: "待办",
  active: "进行中",
  done: "完成",
};

/**
 * The plan area (#139 / pivot item 1).
 *
 * Read-only while the task runs; editable while it is parked ("停下时可改").
 * Saving writes `PUT /tasks/{id}/plan`; the backend seeds the next round from
 * that copy, so the edit takes effect with the next message rather than
 * silently sitting in the UI.
 */
export function PlanPanel({ task }: { task: Task }) {
  const upsert = useTaskStore((s) => s.upsertTask);
  const parked = task.status === "INTERRUPTED";
  const [draft, setDraft] = useState<DraftStep[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // Mirror the server's plan unless the user has unsaved edits to a **parked**
  // task. Once a round starts, the persisted plan is what it runs; showing a
  // stale draft would assert a plan the task does not have.
  useEffect(() => {
    if (dirty && parked) return;
    setDraft(task.plan.map((p) => ({ description: p.description, status: p.status })));
    if (!parked) setDirty(false);
  }, [task.id, task.plan, dirty, parked]);

  const edit = (i: number, patch: Partial<DraftStep>) => {
    setDraft((rows) => rows.map((row, idx) => (idx === i ? { ...row, ...patch } : row)));
    setDirty(true);
    setNote(null);
  };

  const addRow = () => {
    setDraft((rows) => [...rows, { description: "", status: "pending" }]);
    setDirty(true);
  };

  const removeRow = (i: number) => {
    setDraft((rows) => rows.filter((_, idx) => idx !== i));
    setDirty(true);
  };

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      const rows = draft.filter((r) => r.description.trim());
      const r = await putPlan(task.id, rows);
      if (r?.data?.ok) {
        const fresh = await getTask(task.id);
        if (fresh.data) upsert(fresh.data);
        setDirty(false);
        setNote("已保存；下一条消息按新计划跑");
      } else {
        const detail = (r as unknown as { detail?: string }).detail;
        setNote(detail ? "保存被拒绝（任务运行中？）" : "保存失败");
      }
    } catch {
      setNote("保存失败");
    } finally {
      setBusy(false);
    }
  };

  const showHint = task.plan.length > 0 || task.mode === "plan" || parked;
  if (!showHint) return null;

  return (
    <div className="bg-panelMuted/60 rounded-lg p-3 text-sm">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs text-slate-400">计划</span>
        {parked && (
          <button
            onClick={addRow}
            className="text-[11px] text-sky-400 hover:text-sky-300"
          >
            + 添加一步
          </button>
        )}
        {parked && dirty && (
          <button
            onClick={save}
            disabled={busy}
            className="ml-auto text-[11px] px-2 py-0.5 rounded bg-sky-600 hover:bg-sky-500 text-white disabled:opacity-40"
          >
            {busy ? "保存中…" : "保存计划"}
          </button>
        )}
        {note && <span className="ml-auto text-[11px] text-slate-400">{note}</span>}
      </div>

      {draft.length === 0 && (
        <div className="text-xs text-slate-500">
          {task.mode === "plan" ? "计划生成中…" : "还没有计划。切到计划模式问一次，或在这里手动写。"}
        </div>
      )}

      <ol className="space-y-1">
        {draft.map((step, i) => (
          <li key={i} className="flex items-center gap-2">
            <span className="text-slate-500 text-xs w-5 shrink-0">{i + 1}.</span>
            {parked ? (
              <>
                <input
                  value={step.description}
                  onChange={(e) => edit(i, { description: e.target.value })}
                  className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-sm text-slate-100 focus:outline-none focus:border-sky-500"
                />
                <select
                  value={step.status}
                  onChange={(e) => edit(i, { status: e.target.value })}
                  className="bg-slate-900 border border-slate-700 rounded px-1 py-1 text-xs text-slate-300"
                >
                  {Object.entries(STATUS_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => removeRow(i)}
                  className="text-[11px] text-slate-500 hover:text-red-400"
                  title="删除这一步"
                >
                  ✕
                </button>
              </>
            ) : (
              <>
                <span className="flex-1 text-slate-200">{step.description}</span>
                <span className="text-[11px] text-slate-500">
                  {STATUS_LABEL[step.status] ?? step.status}
                </span>
              </>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
