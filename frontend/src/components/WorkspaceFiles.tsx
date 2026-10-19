import { useEffect, useState } from "react";
import { fileDownloadUrl, listFiles, previewFile } from "../api/client";
import { TaskStatus, WorkspaceFile } from "../types";

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

/** C5: the task's workspace files (ADR 0010), with preview and download. */
export function WorkspaceFiles({
  taskId,
  status,
}: {
  taskId: string | null;
  status?: TaskStatus;
}) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ content: string; truncated: boolean } | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
    setPreview(null);
    setNote(null);
    if (!taskId) {
      setFiles([]);
      return;
    }
    listFiles(taskId)
      .then((r) => {
        setFiles(r.data?.files ?? []);
        setTruncated(!!r.data?.truncated);
      })
      .catch(() => setNote("无法读取工作区文件"));
  }, [taskId, status]);

  const open = async (path: string) => {
    if (!taskId) return;
    setNote(null);
    try {
      const r = await previewFile(taskId, path);
      if (r.data) {
        setSelected(path);
        setPreview({ content: r.data.content, truncated: r.data.truncated });
      } else {
        setNote("无法预览这个文件（二进制或不可读），可以直接下载。");
      }
    } catch {
      setNote("无法预览这个文件（二进制或不可读），可以直接下载。");
    }
  };

  if (!taskId) {
    return <div className="mt-3 text-xs text-slate-500">选择一个任务查看工作区文件</div>;
  }

  return (
    <div className="mt-3">
      <div className="text-xs text-slate-400 mb-1">
        工作区文件{files.length ? `（${files.length}）` : ""}
        {truncated && <span className="text-amber-400"> · 只显示前 2000 个</span>}
      </div>
      {files.length === 0 && <div className="text-xs text-slate-500">还没有文件。</div>}
      <div className="space-y-1">
        {files.map((file) => (
          <div
            key={file.path}
            className={`flex items-center gap-2 text-xs rounded px-2 py-1 ${
              selected === file.path ? "bg-slate-800" : "hover:bg-slate-800/60"
            }`}
          >
            <button
              className="flex-1 min-w-0 text-left truncate text-sky-300 hover:underline"
              title={file.path}
              onClick={() => open(file.path)}
            >
              {file.path}
            </button>
            <span className="text-slate-500 shrink-0">{formatSize(file.size)}</span>
            <a
              className="text-green-400 hover:underline shrink-0"
              href={fileDownloadUrl(taskId, file.path)}
              download
            >
              下载
            </a>
          </div>
        ))}
      </div>
      {note && <div className="mt-1 text-xs text-amber-300">{note}</div>}
      {preview && selected && (
        <div className="mt-2 border border-slate-800 rounded">
          <div className="flex items-center gap-2 px-2 py-1 border-b border-slate-800 text-xs">
            <span className="flex-1 truncate text-slate-300" title={selected}>
              {selected}
            </span>
            {preview.truncated && <span className="text-amber-400">已截断</span>}
            <button className="text-slate-400 hover:text-slate-200" onClick={() => setPreview(null)}>
              关闭
            </button>
          </div>
          <pre className="p-2 text-xs text-slate-200 overflow-auto max-h-64 whitespace-pre-wrap">
            {preview.content}
          </pre>
        </div>
      )}
    </div>
  );
}
