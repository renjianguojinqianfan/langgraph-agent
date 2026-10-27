import { useEffect, useRef, useState } from "react";
import { fileDownloadUrl, listFiles, previewFile } from "../../api/client";
import { cx } from "../../lib/cx";
import { formatBytes } from "../../lib/format";
import type { Task, WorkspaceFile } from "../../types";
import { Icon } from "../ui/Icon";
import { fileName, groupFiles } from "./fileGroup";

export function FilePane({ task }: { task: Task }) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<{
    content: string;
    truncated: boolean;
  } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const requestSeq = useRef(0);
  const taskIdRef = useRef(task.id);

  useEffect(() => {
    taskIdRef.current = task.id;
    requestSeq.current += 1;
  }, [task.id]);

  useEffect(() => {
    let cancelled = false;
    setSelected(null);
    setPreview(null);
    setNote(null);
    setFiles([]);
    setTruncated(false);

    const load = () =>
      listFiles(task.id)
        .then((r) => {
          if (cancelled) return;
          if (!r.data) {
            setNote("无法读取工作区文件");
            return;
          }
          setFiles(r.data.files ?? []);
          setTruncated(!!r.data.truncated);
        })
        .catch(() => {
          if (!cancelled) setNote("无法读取工作区文件");
        });

    load();
    // Files appear while the task is still running; refresh until it parks.
    const timer =
      task.status === "RUNNING" ? window.setInterval(load, 2000) : null;
    return () => {
      cancelled = true;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [task.id, task.status]);

  const open = async (path: string) => {
    const seq = ++requestSeq.current;
    const requestedTask = task.id;
    setNote(null);
    try {
      const r = await previewFile(requestedTask, path);
      if (requestedTask !== taskIdRef.current || seq !== requestSeq.current) return;
      if (r.data) {
        setSelected(path);
        setPreview({ content: r.data.content, truncated: r.data.truncated });
      } else {
        setNote("无法预览这个文件（二进制或不可读），可以直接下载。");
      }
    } catch {
      if (requestedTask !== taskIdRef.current || seq !== requestSeq.current) return;
      setNote("无法预览这个文件（二进制或不可读），可以直接下载。");
    }
  };

  const groups = groupFiles(files);

  return (
    <div className="flex flex-col gap-4">
      {truncated && (
        <p className="text-meta text-tertiary">
          文件超过 2000 个，只显示前 2000 个；完整内容请下载。
        </p>
      )}

      {files.length === 0 ? (
        <p className="text-body text-tertiary">
          {task.status === "RUNNING"
            ? "任务运行中；写入工作区的文件会出现在这里。"
            : "工作区还没有文件。任务写下文件后会出现在这里。"}
        </p>
      ) : (
        groups.map((group) => (
          <section key={group.dir || "__root"}>
            <div
              className="mb-1 font-mono text-machine text-tertiary"
              title={group.dir || "工作区根目录"}
            >
              {group.label}
            </div>
            <ul className="flex flex-col">
              {group.files.map((file) => (
                <li key={file.path}>
                  <div
                    className={cx(
                      "flex items-center gap-2 border-l-2 py-1 pl-2 pr-1",
                      selected === file.path
                        ? "border-primary"
                        : "border-transparent hover:bg-surface-hover"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => open(file.path)}
                      className="min-w-0 flex-1 truncate text-left text-body text-primary"
                      title={file.path}
                    >
                      {fileName(file.path)}
                    </button>
                    <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
                      {formatBytes(file.size)}
                    </span>
                    <a
                      href={fileDownloadUrl(task.id, file.path)}
                      download
                      aria-label={`下载 ${file.path}`}
                      className="shrink-0 rounded-control p-1 text-tertiary hover:bg-surface-hover hover:text-primary"
                    >
                      <Icon name="download" size={14} />
                    </a>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}

      {note && <p className="text-meta text-state-danger">{note}</p>}

      {selected && preview && (
        <section className="rounded-well bg-surface-well">
          <div className="flex items-center gap-2 border-b border-rule px-3 py-2">
            <span
              className="min-w-0 flex-1 truncate font-mono text-machine text-signal-now"
              title={selected}
            >
              {selected}
            </span>
            {preview.truncated && (
              <span className="shrink-0 text-meta text-tertiary">已截断</span>
            )}
            <button
              type="button"
              onClick={() => {
                setSelected(null);
                setPreview(null);
              }}
              className="shrink-0 rounded-control p-1 text-tertiary hover:bg-surface-hover hover:text-primary"
              aria-label="关闭预览"
            >
              <Icon name="close" size={14} />
            </button>
          </div>
          <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-all px-3 py-2 font-mono text-code text-secondary">
            {preview.content || "（空文件）"}
          </pre>
        </section>
      )}
    </div>
  );
}
