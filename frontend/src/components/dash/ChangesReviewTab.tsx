import { useEffect, useState } from "react";
import { getTaskChange, getTaskChanges } from "../../api/client";
import {
  changeStatusLabel,
  changesFailure,
  diffMode,
  hasDiffBody,
  reviewKey,
  reviewedCount,
  reviewedText,
  type DiffMode,
} from "../../lib/changes";
import { useChangesReviewStore } from "../../store/changesReviewStore";
import type { ChangeDetail, ChangeFileEntry, ChangesSummary } from "../../types";
import { Button } from "../ui/Button";
import { DiffView } from "./DiffView";
import { FileDownloadLink } from "./FileDownloadLink";

interface ChangesReviewTabProps {
  taskId: string;
  round: number;
  /** Currently selected file path, owned by TaskView (survives tab switches). */
  selectedPath: string;
  onSelectFile: (path: string) => void;
  /** Reuse the existing file-preview tab (#344) instead of a second viewer. */
  onOpenFile: (path: string) => void;
  /** Fetch on first activation only; hidden chips cost no request. */
  active: boolean;
  visible: boolean;
}

type SummaryState =
  | { phase: "loading" }
  | { phase: "ready"; summary: ChangesSummary }
  | { phase: "failed"; status: number; detail: string };

type DetailState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; detail: ChangeDetail }
  | { phase: "failed"; status: number; detail: string };

const EMPTY: string[] = [];

function detailNote(detail: ChangeDetail): string | null {
  if (detail.binary) return "二进制文件：不显示内容与差异，可下载查看。";
  if (detail.oversized) {
    return "文件过大（或整轮超出上限）：不存内容、不计算差异，可下载查看。";
  }
  if (!hasDiffBody(detail)) return "没有可显示的差异。";
  return null;
}

function EmptyHint({ text }: { text: string }) {
  return <p className="px-1 text-meta text-tertiary">{text}</p>;
}

/**
 * One review tab per `(task, round)` (#343). Read-only: file selector,
 * unified/split, wrap, open-in-preview and download. `reviewed` lives in the
 * in-memory store and resets with the task; no apply/reject here.
 */
export function ChangesReviewTab({
  taskId,
  round,
  selectedPath,
  onSelectFile,
  onOpenFile,
  active,
  visible,
}: ChangesReviewTabProps) {
  const [summaryState, setSummaryState] = useState<SummaryState>({ phase: "loading" });
  const [detailState, setDetailState] = useState<DetailState>({ phase: "idle" });
  const [load, setLoad] = useState(active && visible);
  const [reload, setReload] = useState(0);
  const [mode, setMode] = useState<DiffMode>("unified");
  const [wrap, setWrap] = useState(false);
  const key = reviewKey(taskId, round);
  const reviewed = useChangesReviewStore((s) => s.reviewed[key]) ?? EMPTY;
  const toggle = useChangesReviewStore((s) => s.toggle);

  useEffect(() => {
    if (active && visible) setLoad(true);
  }, [active, visible]);

  useEffect(() => {
    if (!load) return;
    let cancelled = false;
    setSummaryState({ phase: "loading" });
    getTaskChanges(taskId, round).then((result) => {
      if (cancelled) return;
      if (result.data) setSummaryState({ phase: "ready", summary: result.data });
      else setSummaryState({ phase: "failed", status: result.status, detail: result.detail });
    });
    return () => {
      cancelled = true;
    };
  }, [taskId, round, load, reload]);

  const summary = summaryState.phase === "ready" ? summaryState.summary : null;
  const selected: ChangeFileEntry | null =
    summary?.files.find((file) => file.path === selectedPath) ?? summary?.files[0] ?? null;
  const selectedKey = selected?.index ?? null;

  useEffect(() => {
    if (!load || selectedKey === null) return;
    let cancelled = false;
    setDetailState({ phase: "loading" });
    getTaskChange(taskId, round, selectedKey).then((result) => {
      if (cancelled) return;
      if (result.data) setDetailState({ phase: "ready", detail: result.data });
      else setDetailState({ phase: "failed", status: result.status, detail: result.detail });
    });
    return () => {
      cancelled = true;
    };
  }, [taskId, round, selectedKey, load, reload]);

  const detail = detailState.phase === "ready" ? detailState.detail : null;
  const note = detail ? detailNote(detail) : null;

  if (summaryState.phase === "loading") {
    return <EmptyHint text="正在读取本轮变更…" />;
  }

  if (summaryState.phase === "failed") {
    const copy = changesFailure(summaryState.status, summaryState.detail);
    return (
      <div role="alert" className="rounded-well border border-rule bg-surface-well px-3 py-3">
        <p className="text-body text-primary">{copy.title}</p>
        <p className="mt-1 text-meta text-tertiary">{copy.hint}</p>
        <Button
          className="mt-2"
          size="sm"
          variant="outline"
          onClick={() => setReload((value) => value + 1)}
        >
          重试
        </Button>
      </div>
    );
  }

  if (!summary || summary.files.length === 0) {
    return <EmptyHint text={`第 ${round} 轮没有 write/edit 变更。`} />;
  }

  const effectiveMode = selected ? diffMode(mode, selected) : mode;
  const reviewedN = reviewedCount(summary.files, reviewed);
  const forceUnified = !!selected && effectiveMode === "unified" && mode === "split";

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="min-w-0 flex-1 text-body text-primary">第 {round} 轮变更</h2>
        <span className="font-mono text-machine tabular-nums text-tertiary">
          {reviewedText(reviewedN, summary.files.length)}
        </span>
      </div>
      <p className="text-meta text-tertiary">
        变更集只包含本轮 write/edit 触达的文件，不是完整工作区 diff。
      </p>
      {summary.truncated && (
        <p className="text-meta text-state-warn">
          文件过多：只列出前 500 个（共 {summary.total} 个）。
        </p>
      )}

      <ul
        aria-label="本轮变更文件"
        className="max-h-44 shrink-0 overflow-y-auto rounded-well border border-rule"
      >
        {summary.files.map((file) => {
          const checked = reviewed.includes(file.path);
          const current = selected?.index === file.index;
          return (
            <li
              key={file.index}
              className={
                current
                  ? "flex min-w-0 items-center gap-2 bg-surface-hover px-2 py-1"
                  : "flex min-w-0 items-center gap-2 px-2 py-1"
              }
            >
              <label className="flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center [touch-action:manipulation]">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(key, file.path)}
                  aria-label={`标记 ${file.path} 已审阅`}
                  className="accent-signal-now"
                />
              </label>
              <button
                type="button"
                onClick={() => onSelectFile(file.path)}
                aria-current={current ? "true" : undefined}
                title={file.path}
                className="min-w-0 flex-1 truncate text-left font-mono text-machine text-secondary hover:text-primary"
              >
                {file.display || file.path}
              </button>
              <span className="shrink-0 text-meta text-tertiary">
                {changeStatusLabel(file)}
              </span>
            </li>
          );
        })}
      </ul>

      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          size="sm"
          variant={effectiveMode === "unified" ? "outline" : "quiet"}
          aria-pressed={effectiveMode === "unified"}
          onClick={() => setMode("unified")}
        >
          合并
        </Button>
        <Button
          size="sm"
          variant={effectiveMode === "split" ? "outline" : "quiet"}
          aria-pressed={effectiveMode === "split"}
          disabled={!!selected && (selected.status !== "modified" || selected.binary || selected.oversized)}
          onClick={() => setMode("split")}
          title={forceUnified ? "单侧变更只能使用合并视图" : undefined}
        >
          并排
        </Button>
        <Button
          size="sm"
          variant={wrap ? "outline" : "quiet"}
          aria-pressed={wrap}
          onClick={() => setWrap((value) => !value)}
        >
          换行
        </Button>
        <span className="min-w-0 flex-1" />
        {selected && (
          <>
            <Button size="sm" variant="outline" onClick={() => onOpenFile(selected.path)}>
              打开文件
            </Button>
            <FileDownloadLink taskId={taskId} path={selected.path} />
          </>
        )}
      </div>

      {selected && <p className="truncate font-mono text-machine text-signal-now">{selected.path}</p>}

      {detailState.phase === "loading" && <EmptyHint text="正在读取差异…" />}

      {detailState.phase === "failed" && (
        <div role="alert" className="rounded-well border border-rule bg-surface-well px-3 py-3">
          <p className="text-body text-primary">
            {changesFailure(detailState.status, detailState.detail).title}
          </p>
          <p className="mt-1 text-meta text-tertiary">
            {changesFailure(detailState.status, detailState.detail).hint}
          </p>
          <Button
            className="mt-2"
            size="sm"
            variant="outline"
            onClick={() => setReload((value) => value + 1)}
          >
            重试
          </Button>
        </div>
      )}

      {detail && (
        <>
          {note && (
            <p className="rounded-well border border-rule bg-surface-well px-3 py-2 text-meta text-tertiary">
              {note}
            </p>
          )}

          {detail.coarse && hasDiffBody(detail) && (
            <p className="text-meta text-state-warn">差异计算超时，已降级为整段替换。</p>
          )}
          {detail.truncated && (
            <p className="text-meta text-state-warn">已截断：只显示前 2000 行差异。</p>
          )}

          {hasDiffBody(detail) && (
            <DiffView detail={detail} mode={effectiveMode} wrap={wrap} />
          )}
        </>
      )}
    </div>
  );
}
