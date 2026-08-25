import { useCallback, useEffect, useState } from "react";
import { deleteKbDoc, listKbDocs, rebuildKb } from "../api/client";
import { KbDoc } from "../types";

/** P1 item 3: knowledge-base management panel (list / rebuild / delete). */
export function KbPanel() {
  const [docs, setDocs] = useState<KbDoc[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await listKbDocs();
      setDocs(r.data?.docs ?? []);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRebuild = async () => {
    setBusy(true);
    try {
      await rebuildKb();
    } catch {
      /* ignore */
    }
    await load();
    setBusy(false);
  };

  const onDelete = async (docId: string) => {
    try {
      await deleteKbDoc(docId);
    } catch {
      /* ignore */
    }
    await load();
  };

  return (
    <aside className="w-64 border-l border-slate-800 flex flex-col">
      <header className="px-4 py-3 border-b border-slate-800 font-semibold text-sm">
        📚 知识库
      </header>
      <div className="flex-1 overflow-auto p-3 space-y-2">
        {docs.length === 0 && (
          <div className="text-xs text-slate-500">暂无文档</div>
        )}
        {docs.map((d) => (
          <div key={d.doc_id} className="rounded border border-slate-800 p-2 text-xs">
            <div className="font-mono truncate" title={d.path}>
              {d.path}
            </div>
            <div className="text-slate-500 mt-0.5">
              {d.chunks} 块 · {d.size} bytes
            </div>
            <button
              onClick={() => onDelete(d.doc_id)}
              className="text-red-400 hover:text-red-300 mt-1"
            >
              删除
            </button>
          </div>
        ))}
      </div>
      <div className="p-3 border-t border-slate-800">
        <button
          onClick={onRebuild}
          disabled={busy}
          className="w-full bg-slate-700 hover:bg-slate-600 rounded py-1.5 text-xs"
        >
          {busy ? "重建中…" : "重建索引"}
        </button>
      </div>
    </aside>
  );
}
