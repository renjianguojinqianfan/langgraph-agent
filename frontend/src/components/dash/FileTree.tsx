import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { cx } from "../../lib/cx";
import {
  buildFileTree,
  fileTreeRows,
  isTreeKey,
  toggleDir,
  treeKeyAction,
  type FileTreeRow,
} from "../../lib/fileTree";
import { filesPaneState, filesPaneStatusText } from "../../lib/failureStates";
import { formatBytes } from "../../lib/format";
import type { Task, WorkspaceFile } from "../../types";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { FileDownloadLink } from "./FileDownloadLink";

export interface FileTreeProps {
  taskId: string;
  taskStatus: Task["status"];
  files: readonly WorkspaceFile[];
  truncated: boolean;
  loading: boolean;
  note: string | null;
  onRetry: () => void;
  /** User expansion set; the host keeps it across polls and surfaces. */
  expanded: ReadonlySet<string>;
  onToggleDir: (path: string) => void;
  activePath: string | null;
  onOpenFile: (path: string) => void;
  query: string;
  onQueryChange: (query: string) => void;
  /** Navigator only: focus the first *visible* row without moving this box. */
  scrollContainer?: RefObject<HTMLElement | null>;
  /** Increment when a host opens the navigator: move focus into the tree once. */
  focusToken?: number;
}

/**
 * #341's tree presentation (status, filter, rows, roving focus) without the
 * polling/expansion state. The Files tab and the #346 navigator both render
 * this component, so there is exactly one tree implementation.
 */
export function FileTree({
  taskId,
  taskStatus,
  files,
  truncated,
  loading,
  note,
  onRetry,
  expanded,
  onToggleDir,
  activePath,
  onOpenFile,
  query,
  onQueryChange,
  scrollContainer,
  focusToken = 0,
}: FileTreeProps) {
  /** Roving tab stop; kept across polls and filter changes when still visible. */
  const [focusPath, setFocusPath] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState<string | null>(null);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  /** Open-time focus that had to wait for the first list to render. */
  const pendingEntry = useRef(false);

  const root = useMemo(() => buildFileTree(files), [files]);
  const rows = useMemo(
    () => fileTreeRows(root, expanded, query),
    [root, expanded, query]
  );
  const tabbable =
    focusPath && rows.some((row) => row.path === focusPath)
      ? focusPath
      : rows[0]?.path;

  // #341: a different task starts from a clean tree, including the roving tab
  // stop. Expansion/filter are reset by the host that owns them.
  useEffect(() => {
    setFocusPath(null);
    setFocusRequest(null);
    pendingEntry.current = false;
  }, [taskId]);

  useEffect(() => {
    if (focusRequest === null) return;
    rowRefs.current.get(focusRequest)?.focus();
    setFocusRequest(null);
  }, [focusRequest]);

  // #346: opening the navigator moves focus into the tree. When the list is
  // still loading, the container (not a tab stop) is the best available stop;
  // once the first list renders, the pending request lands on the first row so
  // ArrowUp/Down work immediately.
  const focusFirstRow = () => {
    const container = scrollContainer?.current;
    let target = tabbable;
    if (container) {
      const view = container.getBoundingClientRect();
      target = rows.find((row) => {
        const el = rowRefs.current.get(row.path);
        if (!el) return false;
        const box = el.getBoundingClientRect();
        return box.bottom > view.top && box.top < view.bottom;
      })?.path ?? tabbable;
    }
    if (!target) return false;
    const row = rowRefs.current.get(target);
    if (!row) return false;
    // Entering the tree must not throw away the remembered scroll position.
    row.focus({ preventScroll: true });
    return true;
  };

  useEffect(() => {
    if (focusToken === 0) return;
    pendingEntry.current = true;
    if (focusFirstRow()) {
      pendingEntry.current = false;
    } else if (searchRef.current) {
      searchRef.current.focus();
      pendingEntry.current = false;
    } else {
      containerRef.current?.focus();
    }
    // `tabbable` is read at trigger time on purpose; the pending effect below
    // handles the case where the list was not there yet.
  }, [focusToken]);

  useEffect(() => {
    if (!pendingEntry.current) return;
    // The user moved focus elsewhere while the list was loading: do not steal.
    if (document.activeElement !== containerRef.current) {
      pendingEntry.current = false;
      return;
    }
    if (focusFirstRow() || !loading) pendingEntry.current = false;
  }, [rows.length, loading]);

  // D-31 / #258 AC5: one face at a time — a failed read never also shows the
  // empty copy (same rule as historyListState, #256).
  const state = filesPaneState({ error: note ?? "", loading, count: files.length });
  const emptyCopy =
    taskStatus === "RUNNING"
      ? "任务运行中；写入工作区的文件会出现在这里。"
      : "工作区还没有文件。任务写下文件后会出现在这里。";
  // #258 终审: a non-empty note wins on every face (rows included) so a failed
  // poll over an already-loaded list still shows the reason + retry.
  let statusText = filesPaneStatusText(state, note ?? "");
  if (!statusText && state === "loading") {
    statusText = "加载中…";
  } else if (!statusText && state === "empty") {
    statusText = emptyCopy;
  }
  const filtered = query.trim().length > 0;

  const activate = (row: FileTreeRow) => {
    if (row.kind === "dir") onToggleDir(row.path);
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
    if (action.toggle && !filtered) onToggleDir(action.toggle);
    if (action.focusPath) setFocusRequest(action.focusPath);
  };

  return (
    <div
      ref={containerRef}
      role="group"
      aria-label="工作区文件树"
      tabIndex={-1}
      className="flex min-w-0 flex-col gap-4"
    >
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
            onClick={onRetry}
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
            ref={searchRef}
            type="text"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
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
              onClick={() => onQueryChange("")}
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
                      <FileDownloadLink taskId={taskId} path={row.path} />
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
