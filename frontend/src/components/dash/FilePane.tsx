import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { listFiles } from "../../api/client";
import { cx } from "../../lib/cx";
import { createFilePollLoop } from "../../lib/filePolling";
import {
  buildFileTree,
  expandAncestors,
  fileTreeRows,
  isTreeKey,
  toggleDir,
  treeKeyAction,
  type FileTreeRow,
} from "../../lib/fileTree";
import { filesLoadFailure, filesPaneState, filesPaneStatusText } from "../../lib/failureStates";
import { formatBytes } from "../../lib/format";
import { shouldPollFiles } from "../../lib/taskState";
import type { Task, WorkspaceFile } from "../../types";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { FileDownloadLink } from "./FileDownloadLink";

/** Refresh cadence while a running task keeps writing files. */
const POLL_MS = 2000;

export function FilePane({
  task,
  visible,
  activePath,
  onOpenFile,
}: {
  task: Task;
  visible: boolean;
  /** The file whose preview tab is active; its ancestors stay expanded. */
  activePath: string | null;
  onOpenFile: (path: string) => void;
}) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  /** Bumped by the retry button; the load effect keys on it (#258 AC5). */
  const [reloadToken, setReloadToken] = useState(0);
  /** Workspace dirs the user opened; the root is always open, so it is absent. */
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [query, setQuery] = useState("");
  /** Roving tab stop; kept across polls and filter changes when still visible. */
  const [focusPath, setFocusPath] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<string | null>(null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  const paneRef = useRef<HTMLDivElement>(null);

  // A different task starts from a clean pane; a status change must not clear
  // the list the user is reading.
  useEffect(() => {
    setFiles([]);
    setTruncated(false);
    setNote(null);
    setLoading(true);
    setExpanded(new Set());
    setQuery("");
    setFocusPath(null);
    setFocusRequest(null);
    // #341: switching tasks also resets scroll. The scroll owner is the tab
    // panel that keeps this list mounted, not this component.
    paneRef.current
      ?.closest<HTMLElement>('[role="tabpanel"]')
      ?.scrollTo({ top: 0 });
  }, [task.id]);

  // #341: opening a file (from the tree or elsewhere) reveals it, even when
  // its directory was collapsed. Polling the same path does not touch this.
  useEffect(() => {
    if (!activePath) return;
    setExpanded((current) => expandAncestors(current, activePath));
  }, [activePath, task.id]);

  useEffect(() => {
    // #230: a closed drawer (narrow screens) must not fetch. Reopening re-runs
    // this effect (visible flips), so the first load happens immediately then.
    if (!visible) return;

    setLoading(true);
    // #335: poll only after each request settles, so a slow scan cannot stack
    // requests. `stop()` drops the browser-side response of an in-flight scan
    // (task switch / hidden / retry) but cannot stop the server-side scan.
    const controller = new AbortController();
    const loop = createFilePollLoop({
      intervalMs: POLL_MS,
      run: () => listFiles(task.id, controller.signal),
      shouldContinue: () => shouldPollFiles(task.status, true),
      apply: (r) => {
        setLoading(false);
        if (!r.data) {
          setNote(filesLoadFailure(r.status, r.detail));
          return;
        }
        setNote(null);
        setFiles(r.data.files ?? []);
        setTruncated(!!r.data.truncated);
      },
      // `reqWithStatus` never rejects (a transport failure already comes back
      // as status 0) — kept as defence in depth, not a silent dead path.
      onError: () => {
        setLoading(false);
        setNote(filesLoadFailure(0));
      },
    });
    loop.start();
    return () => {
      // Invalidate first, then cancel the browser request: an abort rejection
      // must not resurrect an error note for a pane that already moved on.
      loop.stop();
      controller.abort();
    };
  }, [task.id, task.status, visible, reloadToken]);

  const root = useMemo(() => buildFileTree(files), [files]);
  const rows = useMemo(
    () => fileTreeRows(root, expanded, query),
    [root, expanded, query]
  );
  const tabbable =
    focusPath && rows.some((row) => row.path === focusPath)
      ? focusPath
      : rows[0]?.path;

  useEffect(() => {
    if (focusRequest === null) return;
    rowRefs.current.get(focusRequest)?.focus();
    setFocusRequest(null);
  }, [focusRequest]);

  // D-31 / #258 AC5: one face at a time — a failed read never also shows the
  // empty copy (same rule as historyListState, #256).
  const state = filesPaneState({ error: note ?? "", loading, count: files.length });
  const emptyCopy =
    task.status === "RUNNING"
      ? "任务运行中；写入工作区的文件会出现在这里。"
      : "工作区还没有文件。任务写下文件后会出现在这里。";
  // #258 终审: a non-empty note wins on every face (rows included) so a failed
  // poll over an already-loaded list still shows the reason + retry.
  const statusText =
    filesPaneStatusText(state, note ?? "") ||
    (state === "loading" ? "加载中…" : state === "empty" ? emptyCopy : "");
  const filtered = query.trim().length > 0;

  const activate = (row: FileTreeRow) => {
    if (row.kind === "dir") setExpanded((current) => toggleDir(current, row.path));
    else onOpenFile(row.path);
  };

  const onTreeKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    // Nested links keep their own Enter / Tab behaviour.
    if (target.closest("a,button,input") !== null) return;
    const path = target.closest<HTMLElement>("[data-tree-path]")?.dataset.treePath;
    if (!path) return;
    const row = rows.find((item) => item.path === path);
    if (!row) return;

    if (event.key === "Enter") {
      event.preventDefault();
      activate(row);
      return;
    }
    if (!isTreeKey(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    const action = treeKeyAction(rows, path, event.key);
    // Filtered rows are all rendered open; leave the saved expansion untouched
    // so clearing the filter restores exactly what the user had before.
    const { toggle } = action;
    if (toggle && !filtered) {
      setExpanded((current) => toggleDir(current, toggle));
    }
    if (action.focusPath) setFocusRequest(action.focusPath);
  };

  return (
    <div ref={paneRef} className="flex min-w-0 flex-col gap-4">
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
        className={cx(
          "flex flex-wrap items-center gap-2",
          state === "rows" && !note && "sr-only"
        )}
      >
        <p
          className={cx(
            "min-w-0 flex-1",
            note ? "text-meta text-state-danger" : "text-body text-tertiary"
          )}
        >
          {statusText}
        </p>
        {note && (
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
        <div role="search" className="relative">
          <Icon
            name="search"
            size={14}
            className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-tertiary"
          />
          <input
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="按名称或路径过滤文件"
            placeholder="按名称或路径过滤"
            autoComplete="off"
            spellCheck={false}
            className="h-8 w-full rounded-control bg-surface-well pl-7 pr-7 text-meta text-primary placeholder:text-tertiary"
          />
          {query && (
            <button
              type="button"
              aria-label="清除过滤"
              onClick={() => setQuery("")}
              className="absolute right-1 top-1/2 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-control text-tertiary hover:bg-surface-hover hover:text-primary"
            >
              <Icon name="close" size={12} />
            </button>
          )}
        </div>
      )}

      {state === "rows" && filtered && rows.length === 0 && (
        <p role="status" className="text-body text-tertiary">
          没有匹配「{query.trim()}」的文件。
        </p>
      )}

      {state === "rows" && rows.length > 0 && (
        <div role="tree" aria-label="工作区文件" className="flex flex-col" onKeyDown={onTreeKeyDown}>
          {rows.map((row) => {
            const current = row.kind === "file" && row.path === activePath;
            return (
              <div
                key={row.path}
                ref={(el) => {
                  if (el) rowRefs.current.set(row.path, el);
                  else rowRefs.current.delete(row.path);
                }}
                role="treeitem"
                data-tree-path={row.path}
                aria-level={row.depth + 1}
                aria-expanded={row.kind === "dir" ? row.expanded : undefined}
                aria-selected={row.kind === "file" ? current : undefined}
                tabIndex={row.path === tabbable ? 0 : -1}
                title={row.path}
                style={{ paddingLeft: 4 + row.depth * 12 }}
                onFocus={() => setFocusPath(row.path)}
                onClick={() => {
                  setFocusPath(row.path);
                  activate(row);
                }}
                className={cx(
                  "flex min-h-6 min-w-0 cursor-pointer items-center gap-1 rounded-control py-0.5 pr-1 text-body hover:bg-surface-hover",
                  row.kind === "dir" ? "text-secondary" : "text-primary",
                  current && "text-signal-now"
                )}
              >
                {row.kind === "dir" ? (
                  <Icon
                    name="chevron"
                    size={12}
                    className={cx(
                      "text-tertiary transition-transform duration-150",
                      !row.expanded && "-rotate-90"
                    )}
                  />
                ) : (
                  <Icon name="file" size={12} className="text-tertiary" />
                )}
                <span className="min-w-0 flex-1 truncate text-left">{row.name}</span>
                {row.kind === "file" && (
                  <>
                    <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
                      {formatBytes(row.size)}
                    </span>
                    <span className="shrink-0" onClick={(event) => event.stopPropagation()}>
                      <FileDownloadLink taskId={task.id} path={row.path} />
                    </span>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
