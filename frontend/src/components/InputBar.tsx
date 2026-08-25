import { useState } from "react";
import { createTask } from "../api/client";
import { useTaskStore } from "../store/taskStore";

export function InputBar() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const setCurrent = useTaskStore((s) => s.setCurrentTask);

  const onSubmit = async () => {
    const input = text.trim();
    if (!input || busy) return;
    setBusy(true);
    setText("");
    try {
      const res = await createTask({ input });
      const id = res.data?.task_id;
      if (id) setCurrent(id);
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-t border-slate-800 p-3">
      <div className="flex gap-2">
        <textarea
          className="flex-1 resize-none bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-sky-500"
          rows={2}
          placeholder="用自然语言描述一个任务，例如：联网查资料并写一份报告…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSubmit();
          }}
        />
        <button
          onClick={onSubmit}
          disabled={busy || !text.trim()}
          className="px-4 rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-40 text-white text-sm"
        >
          发送
        </button>
      </div>
      <div className="text-[11px] text-slate-500 mt-1">
        Ctrl/⌘ + Enter 发送
      </div>
    </div>
  );
}
