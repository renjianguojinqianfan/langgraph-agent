import { useEffect, useRef, useState } from "react";
import { apiErrorText, deleteWorkspaceTasks } from "../../api/client";
import { historyListState, listBannerVisible, listEmptyTitle } from "../../lib/failureStates";
import { useTaskStore } from "../../store/taskStore";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Segmented } from "../ui/Segmented";
import { ReauthAction } from "../ReauthAction";
import { filterByQuery, railGroups, type RailFilter } from "./group";
import { RailFooter } from "./RailFooter";
import { RailSearch } from "./RailSearch";
import { TaskRow } from "./TaskRow";

const FILTER_OPTIONS: Array<{ value: RailFilter; label: string }> = [
  { value: "current", label: "本目录" },
  { value: "all", label: "全部" },
];

interface PendingDelete {
  workspace: string;
  label: string;
  count: number;
}

export function TaskRail() {
  const tasks = useTaskStore((s) => s.tasks);
  const listError = useTaskStore((s) => s.listError);
  const listLoading = useTaskStore((s) => s.listLoading);
  // Per-source (复审 LOW): only the list read's own 401 drives this rail's
  // re-login button — a detail 401 must not relabel a list transport failure.
  const listAuthExpired = useTaskStore((s) => s.authExpired.list);
  const retryList = useTaskStore((s) => s.retryList);
  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const setCurrentTask = useTaskStore((s) => s.setCurrentTask);
  const removeWorkspaceTasks = useTaskStore((s) => s.removeWorkspaceTasks);
  const filter = useTaskStore((s) => s.historyFilter);
  const setFilter = useTaskStore((s) => s.setHistoryFilter);
  const [query, setQuery] = useState("");
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleteNote, setDeleteNote] = useState("");
  const [deleting, setDeleting] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);

  const visible = filterByQuery(tasks, query);
  const groups = railGroups(visible, filter);
  // #256: which face the list may show. With rows from the last success the
  // failure stays a one-line banner above real rows; with none it owns the
  // pane — the empty copy would read as "no tasks here" (the #256 bug).
  const listState = historyListState({
    error: listError,
    loading: listLoading,
    rows: tasks.length,
    visible: visible.length,
  });
  // Count from the unfiltered list: the dialog describes the whole directory,
  // not just the rows the search box currently shows (F2).
  const directoryCounts = new Map<string, number>();
  for (const task of tasks) {
    const workspace = task.workspace.trim();
    directoryCounts.set(workspace, (directoryCounts.get(workspace) ?? 0) + 1);
  }

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (pendingDelete && !dialog.open) dialog.showModal();
    else if (!pendingDelete && dialog.open) dialog.close();
  }, [pendingDelete]);

  const armDirectoryDelete = (workspace: string, label: string, count: number) => {
    setDeleteNote("");
    setPendingDelete({ workspace, label, count });
  };

  const confirmDelete = async () => {
    if (!pendingDelete || deleting) return;
    setDeleting(true);
    setDeleteNote("");
    try {
      const response = await deleteWorkspaceTasks(pendingDelete.workspace);
      if (response.data) {
        removeWorkspaceTasks(pendingDelete.workspace);
        setPendingDelete(null);
      } else {
        setDeleteNote(apiErrorText(response));
      }
    } catch {
      setDeleteNote("删除失败：无法连接后台。");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-2 px-3 pb-3 pt-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-title text-primary">任务</h2>
          <Button size="sm" variant="quiet" onClick={() => setCurrentTask(null)}>
            <Icon name="plus" size={14} />
            新任务
          </Button>
        </div>
        <RailSearch value={query} onChange={setQuery} />
        <Segmented
          options={FILTER_OPTIONS}
          value={filter}
          onChange={setFilter}
          label="任务范围"
        />
        {listBannerVisible(listError, listState) && (
          // AC1 + 复审 LOW: a failed refetch keeps the stale rows, but the
          // banner still carries its retry entry — text alone left the user
          // with no way out. The predicate keeps it visible when the search
          // box filters every row away (state reads "empty" then).
          <div className="flex items-center gap-2">
            <p role="status" className="min-w-0 flex-1 text-meta text-state-danger">
              {listError}
            </p>
            {listAuthExpired ? <ReauthAction /> : (
              <Button variant="outline" size="sm" onClick={retryList}>
                重试
              </Button>
            )}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-2">
        {listState === "error" ? (
          <div className="px-3 py-6">
            <p role="alert" className="text-body text-state-danger">
              {listError}
            </p>
            <p className="mt-1 text-meta text-tertiary">
              {listAuthExpired
                ? "重新登录后会回到这里；列表不会显示为空。"
                : "确认后台在运行后重试；列表不会显示为空。"}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {listAuthExpired ? (
                <ReauthAction />
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={retryList}
                >
                  重试
                </Button>
              )}
            </div>
          </div>
        ) : listState === "loading" ? (
          <div className="px-3 py-6">
            <p className="text-body text-tertiary">加载中…</p>
          </div>
        ) : listState === "empty" ? (
          <div className="px-3 py-6">
            {/* #256 (复审 LOW-1): "rows exist, none match the query" is a
                different truth from "no tasks at all" — and with a failed
                refetch on top, "这里还没有任务" was half a lie. */}
            <p className="text-body text-secondary">{listEmptyTitle(tasks.length)}</p>
            <p className="mt-1 text-meta text-tertiary">
              {query.trim() ? "换个关键词试试。" : "在下方输入开始。"}
            </p>
          </div>
        ) : (
          groups.map((group, index) => (
            <div key={`ws:${group.workspace}`} className={index > 0 ? "mt-2" : ""}>
              {group.label && (
                <div
                  title={group.workspace || "默认目录"}
                  className="flex items-center gap-1.5 px-2.5 pb-1 pt-2 font-mono text-machine text-tertiary"
                >
                  <Icon name="folder" size={12} />
                  <span className="min-w-0 flex-1 truncate">{group.label}</span>
                  <IconButton
                    size="sm"
                    icon="trash"
                    aria-label={`删除目录历史：${group.label}`}
                    title="只删除该目录的历史记录"
                    onClick={() =>
                      armDirectoryDelete(
                        group.workspace,
                        group.label,
                        directoryCounts.get(group.workspace.trim()) ??
                          group.tasks.length
                      )
                    }
                  />
                </div>
              )}
              <div className="flex flex-col gap-0.5">
                {group.tasks.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    selected={task.id === currentTaskId}
                    showWorkspace={filter === "all"}
                    onSelect={setCurrentTask}
                  />
                ))}
              </div>
            </div>
          ))
        )}
      </div>

      <RailFooter />

      <dialog
        ref={dialogRef}
        aria-labelledby="delete-workspace-title"
        onClose={() => setPendingDelete(null)}
        onClick={(event) => {
          if (event.target === dialogRef.current) dialogRef.current?.close();
        }}
        className="m-auto w-[min(520px,calc(100vw-2rem))] rounded-dialog border border-rule bg-surface-column p-0 text-primary backdrop:bg-surface-page/80"
      >
        <div className="flex flex-col gap-3 p-5">
          <h2 id="delete-workspace-title" className="text-title">
            删除目录历史
          </h2>
          <p className="text-body text-secondary">
            只删除「{pendingDelete?.label}」目录的全部历史记录（记录 + checkpoint
            + trace；当前列表显示 {pendingDelete?.count} 条），
            <strong>不会删除磁盘文件</strong>；这些任务名下的后台进程会一并终止，
            运行中的任务会让整批删除被拒绝。
          </p>
          {deleteNote && (
            <p role="alert" className="text-meta text-state-danger">
              {deleteNote}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" onClick={() => setPendingDelete(null)}>
              取消
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={confirmDelete}
              disabled={deleting}
            >
              {deleting ? "删除中…" : "删除"}
            </Button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
