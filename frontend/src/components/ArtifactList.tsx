import { artifactUrl, previewUrl } from "../api/client";
import { Task } from "../types";

export function ArtifactList({ task }: { task: Task }) {
  if (task.artifacts.length === 0) return null;
  return (
    <div className="mt-3">
      <div className="text-xs text-slate-400 mb-1">产物文件</div>
      <div className="flex flex-wrap gap-2">
        {task.artifacts.map((a) => (
          <div
            key={a.id}
            className="flex items-center gap-2 text-xs bg-slate-800 rounded px-2 py-1"
          >
            <span className="truncate max-w-[160px]">{a.filename}</span>
            <a
              className="text-sky-400 hover:underline"
              href={previewUrl(task.id, a.id)}
              target="_blank"
              rel="noreferrer"
            >
              预览
            </a>
            <a
              className="text-green-400 hover:underline"
              href={artifactUrl(task.id, a.id)}
              download={a.filename}
            >
              下载
            </a>
          </div>
        ))}
      </div>
    </div>
  );
}
