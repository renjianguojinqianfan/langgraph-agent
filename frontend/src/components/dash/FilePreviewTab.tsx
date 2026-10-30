import { useEffect, useState } from "react";
import { previewFile } from "../../api/client";
import { formatBytes } from "../../lib/format";
import { previewFailure, type PreviewFailureCopy } from "../../lib/preview";
import type { FilePreview } from "../../types";
import { FileDownloadLink } from "./FileDownloadLink";

type State =
  | { phase: "loading" }
  | { phase: "ready"; preview: FilePreview }
  | { phase: "failed"; copy: PreviewFailureCopy };

interface FilePreviewTabProps {
  taskId: string;
  path: string;
  /** Fetch on first activation, not on mount: hidden chips cost no request. */
  active: boolean;
  /** Narrow-screen drawer: a closed drawer must not fetch (#230). */
  visible: boolean;
}

/**
 * One file-preview tab (#266): the third column switches to this body instead
 * of appending a preview below the file list. The download link is present in
 * every state, including failures.
 */
export function FilePreviewTab({
  taskId,
  path,
  active,
  visible,
}: FilePreviewTabProps) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [load, setLoad] = useState(active && visible);

  useEffect(() => {
    if (active && visible) setLoad(true);
  }, [active, visible]);

  useEffect(() => {
    if (!load) return;
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
  }, [taskId, path, load]);

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
        <FileDownloadLink taskId={taskId} path={path} />
      </div>

      {state.phase === "loading" && <p className="text-body text-tertiary">加载中…</p>}

      {state.phase === "failed" && (
        <div role="alert" className="rounded-well border border-rule bg-surface-well px-3 py-3">
          <p className="text-body text-primary">{state.copy.title}</p>
          <p className="mt-1 text-meta text-tertiary">{state.copy.hint}</p>
          <FileDownloadLink taskId={taskId} path={path} labeled />
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
