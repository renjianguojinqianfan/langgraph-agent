import { useEffect, useMemo, useRef, useState } from "react";
import { getTaskChange, getTaskChanges } from "../../api/client";
import {
  changeCard,
  changeCountsText,
  changeStatusLabel,
  changesTruncatedText,
  createPreviewGate,
  diffPrefix,
  flattenHunks,
} from "../../lib/changes";
import { DIFF_TONE } from "../../lib/diffTone";
import type { ChangeDetail, ChangesSummary } from "../../types";

interface ChangeCardProps {
  taskId: string;
  round: number;
  /** Open (or re-activate) the one review tab for this round. */
  onOpenChanges: (round: number, path?: string) => void;
}

type State =
  | { phase: "loading" }
  | { phase: "ready"; summary: ChangesSummary }
  | { phase: "failed" };

const PREVIEW_LINES = 14;
const HOVER_DELAY_MS = 500;

/**
 * Turn-tail changed-files card (#343).
 *
 * One card per completed round, only when the round produced changes. It is a
 * summary, not a diff: the full body lives in the review tab. Hovering a row
 * for 500ms shows a capped unified preview.
 */
export function ChangeCard({ taskId, round, onOpenChanges }: ChangeCardProps) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [expanded, setExpanded] = useState(false);
  const [preview, setPreview] = useState<{ path: string; detail: ChangeDetail } | null>(
    null
  );
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gate = useRef(createPreviewGate());

  useEffect(
    () => () => {
      gate.current.invalidate();
      if (timer.current !== null) clearTimeout(timer.current);
    },
    []
  );

  useEffect(() => {
    let cancelled = false;
    setState({ phase: "loading" });
    getTaskChanges(taskId, round).then((result) => {
      if (cancelled) return;
      if (result.data) setState({ phase: "ready", summary: result.data });
      else setState({ phase: "failed" });
    });
    return () => {
      cancelled = true;
    };
  }, [taskId, round]);

  const cancelPreview = () => {
    gate.current.invalidate();
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    setPreview(null);
  };

  const schedulePreview = (path: string, index: number) => {
    cancelPreview();
    const generation = gate.current.next();
    timer.current = setTimeout(() => {
      timer.current = null;
      getTaskChange(taskId, round, index).then((result) => {
        if (result.data && gate.current.isCurrent(generation)) {
          setPreview({ path, detail: result.data });
        }
      });
    }, HOVER_DELAY_MS);
  };

  const previewRows = useMemo(
    () => (preview ? flattenHunks(preview.detail.hunks) : []),
    [preview]
  );

  if (state.phase !== "ready" || state.summary.total === 0) return null;
  const model = changeCard(state.summary.files, state.summary.total, expanded);

  return (
    <section
      aria-label={`第 ${round} 轮变更`}
      className="relative rounded-well border border-rule bg-surface-sheet"
      onMouseLeave={cancelPreview}
    >
      <button
        type="button"
        onClick={() => onOpenChanges(round)}
        className="flex w-full min-w-0 items-center gap-2 rounded-t-well px-3 py-2 text-left hover:bg-surface-hover"
      >
        <span className="min-w-0 flex-1 truncate text-body text-primary">
          {model.header}
        </span>
        <span className="shrink-0 text-meta tabular-nums text-tertiary">
          {state.summary.added > 0 || state.summary.deleted > 0
            ? changeCountsText(state.summary.added, state.summary.deleted)
            : ""}
        </span>
        <span aria-hidden="true" className="shrink-0 text-meta text-tertiary">
          →
        </span>
      </button>

      <ul className="border-t border-rule">
        {model.rows.map((file) => (
          <li key={file.index} className="relative">
            <button
              type="button"
              onClick={() => onOpenChanges(round, file.path)}
              onMouseEnter={() => schedulePreview(file.path, file.index)}
              onFocus={() => schedulePreview(file.path, file.index)}
              onMouseLeave={cancelPreview}
              onBlur={cancelPreview}
              className="flex w-full min-w-0 items-center gap-2 px-3 py-1.5 text-left hover:bg-surface-hover"
              title={file.path}
            >
              <span className="min-w-0 flex-1 truncate font-mono text-machine text-secondary">
                {file.display || file.path}
              </span>
              <span className="shrink-0 text-meta text-tertiary">
                {changeStatusLabel(file)}
              </span>
              {!file.binary && !file.oversized && (
                <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
                  {changeCountsText(file.added, file.deleted)}
                </span>
              )}
            </button>

            {preview?.path === file.path && (
              <div
                aria-hidden="true"
                className="pointer-events-none absolute left-2 right-2 top-full z-10 max-h-[420px] overflow-hidden rounded-well border border-rule bg-surface-column p-2 shadow-lg"
              >
                <p className="mb-1 truncate font-mono text-machine text-tertiary">
                  {file.path}
                </p>
                {preview.detail.binary || preview.detail.oversized ? (
                  <p className="text-meta text-tertiary">
                    {changeStatusLabel(file)}，不显示 diff；打开 review tab 查看。
                  </p>
                ) : (
                  <div className="overflow-hidden font-mono text-code">
                    {previewRows.slice(0, PREVIEW_LINES).map((row, i) => (
                      <div
                        key={i}
                        className={`whitespace-pre ${DIFF_TONE[row.kind]}`}
                      >
                        {diffPrefix(row.kind)}
                        {row.text}
                      </div>
                    ))}
                    {previewRows.length > PREVIEW_LINES && (
                      <p className="pt-1 text-meta text-tertiary">预览已截断</p>
                    )}
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>

      {state.summary.truncated && (
        <p className="border-t border-rule px-3 py-1.5 text-meta text-state-warn">
          {changesTruncatedText(state.summary.total)}
        </p>
      )}

      {model.hasMore && (
        <div className="border-t border-rule px-3 py-1.5">
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
            className="text-meta text-secondary hover:text-primary"
          >
            {expanded ? "收起" : `展开其余 ${model.hidden} 个文件`}
          </button>
        </div>
      )}
    </section>
  );
}
