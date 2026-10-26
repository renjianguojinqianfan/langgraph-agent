import { useEffect, useState } from "react";
import { getPlan, putPlan } from "../api/client";
import { Task } from "../types";

/**
 * The plan area (C6): the task's plan is one Markdown file in its workspace.
 *
 * Read-only while the task runs; a parked or failed task can edit the whole
 * text and save it back to the file (B1 #219). The raw Markdown is shown, not
 * rendered, and an external edit to the file is picked up on the next round.
 */
export function PlanPanel({ task }: { task: Task }) {
  const parked = task.status === "INTERRUPTED" || task.status === "FAILED";
  const [text, setText] = useState("");
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (editing) return;
    getPlan(task.id)
      .then((r) => {
        if (r.data) {
          setText(r.data.text);
          setVersion(r.data.version);
        }
      })
      .catch(() => {});
  }, [task.id, task.plan_version, task.status, editing]);

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await putPlan(task.id, text);
      if (r?.data?.ok) {
        setVersion(r.data.version);
        setEditing(false);
        setNote("已保存；下一条消息按新计划跑");
      } else {
        setNote("保存被拒绝（任务运行中？）");
      }
    } catch {
      setNote("保存失败");
    } finally {
      setBusy(false);
    }
  };

  const showHint = version > 0 || text.trim().length > 0 || task.mode === "plan" || parked;
  if (!showHint) return null;

  return (
    <div className="bg-panelMuted/60 rounded-lg p-3 text-sm">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs text-slate-400">计划</span>
        {version > 0 && (
          <span className="text-[11px] text-slate-500">计划已更新到第 {version} 版</span>
        )}
        {parked && !editing && (
          <button
            onClick={() => {
              setEditing(true);
              setNote(null);
            }}
            className="ml-auto text-[11px] text-sky-400 hover:text-sky-300"
          >
            编辑
          </button>
        )}
        {editing && (
          <>
            <button
              onClick={save}
              disabled={busy}
              className="ml-auto text-[11px] px-2 py-0.5 rounded bg-sky-600 hover:bg-sky-500 text-white disabled:opacity-40"
            >
              {busy ? "保存中…" : "保存计划"}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setNote(null);
              }}
              disabled={busy}
              className="text-[11px] text-slate-400 hover:text-slate-200"
            >
              取消
            </button>
          </>
        )}
        {note && <span className="ml-auto text-[11px] text-slate-400">{note}</span>}
      </div>

      {editing ? (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={12}
          className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-100 focus:outline-none focus:border-sky-500"
        />
      ) : text.trim() ? (
        <pre className="text-xs font-mono text-slate-200 whitespace-pre-wrap max-h-64 overflow-auto">
          {text}
        </pre>
      ) : (
        <div className="text-xs text-slate-500">
          {task.mode === "plan" ? "计划生成中…" : "还没有计划。切到计划模式问一次，或停下来手动写。"}
        </div>
      )}
    </div>
  );
}
