import { useEffect, useState } from "react";
import { fileDownloadUrl, previewFile } from "../../api/client";
import { formatBytes } from "../../lib/format";
import { previewFailure, type PreviewFailureCopy } from "../../lib/preview";
import type { FilePreview } from "../../types";
import { Icon } from "../ui/Icon";

type State =
  | { phase: "loading" }
  | { phase: "ready"; preview: FilePreview }
  | { phase: "failed"; copy: PreviewFailureCopy };

/**
 * One file-preview tab (#266): the third column switches to this body instead
 * of appending a preview below the file list. The download link is present in
 * every state, including failures.
 */
export function FilePreviewTab({ taskId, path }: { taskId: string; path: string }) {
  const [state, setState] = useState<State>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ phase: "loading" });
    previewFile(taskId, path)
      .then((result) => {
        if (cancelled) return;
        if (result.data) setState({ phase: "ready", preview: result.data });
        else setState({ phase: "failed", copy: previewFailure(result.status, result.detail) });
      })
      .catch(() => {
        if (!cancelled) setState({ phase: "failed", copy: previewFailure(0) });
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, path]);

  const download = fileDownloadUrl(taskId, path);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-w-0 items-center gap-2">
        <span
          className="min-w-0 flex-1 truncate font-mono text-machine text-signal-now"
          title={path}
        >
          {path}
        </span>
        {state.phase === "ready" && (
          <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
            {formatBytes(state.preview.size)}
          </span>
        )}
        <a
          href={download}
          download
          aria-label={`下载 ${path}`}
          title="下载"
          className="shrink-0 rounded-control p-1.5 text-tertiary hover:bg-surface-hover hover:text-primary"
        >
          <Icon name="download" size={14} />
        </a>
      </div>

      {state.phase === "loading" && <p className="text-body text-tertiary">加载中…</p>}

      {state.phase === "failed" && (
        <div role="alert" className="rounded-well border border-rule bg-surface-well px-3 py-3">
          <p className="text-body text-primary">{state.copy.title}</p>
          <p className="mt-1 text-meta text-tertiary">{state.copy.hint}</p>
          <a
            href={download}
            download
            className="mt-2 inline-flex items-center gap-1.5 rounded-control border border-rule px-2 py-1 text-meta text-primary hover:bg-surface-hover"
          >
            <Icon name="download" size={14} />
            下载文件
          </a>
        </div>
      )}

      {state.phase === "ready" && (
        <>
          {state.preview.truncated && (
            <p className="text-meta text-state-warn">
              已截断：只显示前 200KB，完整内容请下载。
            </p>
          )}
          <pre className="whitespace-pre-wrap break-all rounded-well bg-surface-well px-3 py-2 font-mono text-code text-secondary">
            {state.preview.content || "（空文件）"}
          </pre>
        </>
      )}
    </div>
  );
}
