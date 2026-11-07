import { useEffect, useState } from "react";
import { listFiles } from "../../api/client";
import { cx } from "../../lib/cx";
import { filesLoadFailure, filesPaneState } from "../../lib/failureStates";
import { formatBytes } from "../../lib/format";
import { shouldPollFiles } from "../../lib/taskState";
import type { Task, WorkspaceFile } from "../../types";
import { Button } from "../ui/Button";
import { FileDownloadLink } from "./FileDownloadLink";
import { fileName, groupFiles } from "./fileGroup";

/** Refresh cadence while a running task keeps writing files. */
const POLL_MS = 2000;

export function FilePane({
  task,
  visible,
  onOpenFile,
}: {
  task: Task;
  visible: boolean;
  onOpenFile: (path: string) => void;
}) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  /** Bumped by the retry button; the load effect keys on it (#258 AC5). */
  const [reloadToken, setReloadToken] = useState(0);

  // A different task starts from a clean pane; a status change must not clear
  // the list the user is reading.
  useEffect(() => {
    setFiles([]);
    setTruncated(false);
    setNote(null);
    setLoading(true);
  }, [task.id]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    const load = () =>
      listFiles(task.id)
        .then((r) => {
          if (cancelled) return;
          if (!r.data) {
            setNote(filesLoadFailure(r.status, r.detail));
            return;
          }
          setNote(null);
          setFiles(r.data.files ?? []);
          setTruncated(!!r.data.truncated);
        })
        // `reqWithStatus` never rejects (a transport failure already comes back
        // as status 0) — kept as defence in depth, not a silent dead path.
        .catch(() => {
          if (!cancelled) setNote(filesLoadFailure(0));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });

    // #230: a closed drawer (narrow screens) must not fetch. Reopening re-runs
    // this effect (visible flips), so the first load happens immediately then.
    if (visible) load();
    // Files appear while the task is still running; refresh until it parks.
    const timer = shouldPollFiles(task.status, visible)
      ? window.setInterval(load, POLL_MS)
      : null;
    return () => {
      cancelled = true;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [task.id, task.status, visible, reloadToken]);

  const groups = groupFiles(files);
  // D-31 / #258 AC5: one face at a time — a failed read never also shows the
  // empty copy (same rule as historyListState, #256).
  const state = filesPaneState({ error: note ?? "", loading, count: files.length });
  const emptyCopy =
    task.status === "RUNNING"
      ? "任务运行中；写入工作区的文件会出现在这里。"
      : "工作区还没有文件。任务写下文件后会出现在这里。";
  const statusText =
    state === "error"
      ? (note ?? "")
      : state === "loading"
        ? "加载中…"
        : state === "empty"
          ? emptyCopy
          : "";

  return (
    <div className="flex flex-col gap-4">
      {truncated && (
        <p className="text-meta text-tertiary">
          文件超过 2000 个，只显示前 2000 个；完整内容请下载。
        </p>
      )}

      {/* Persistent live region (D-24 / AC5): inserting a populated node is not
          announced by most screen readers — an always-mounted region is. It is
          the only status face (error / loading / empty); rows render below. */}
      <div
        role="status"
        aria-live="polite"
        className={cx("flex flex-wrap items-center gap-2", state === "rows" && "sr-only")}
      >
        <p
          className={cx(
            "min-w-0 flex-1",
            note ? "text-meta text-state-danger" : "text-body text-tertiary"
          )}
        >
          {statusText}
        </p>
        {state === "error" && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setReloadToken((n) => n + 1)}
            disabled={loading}
          >
            {loading ? "重试中…" : "重试"}
          </Button>
        )}
      </div>

      {state === "rows" && (
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
                  <div className="flex items-center gap-2 py-1 pl-2 pr-1 hover:bg-surface-hover">
                    <button
                      type="button"
                      onClick={() => onOpenFile(file.path)}
                      className="min-w-0 flex-1 truncate text-left text-body text-primary"
                      title={file.path}
                    >
                      {fileName(file.path)}
                    </button>
                    <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
                      {formatBytes(file.size)}
                    </span>
                    <FileDownloadLink taskId={task.id} path={file.path} />
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}

    </div>
  );
}
