import { useEffect, useRef, useState } from "react";
import { apiErrorText, deleteWorkspaceTasks } from "../../api/client";
import { useTaskStore } from "../../store/taskStore";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Segmented } from "../ui/Segmented";
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
        {listError && (
          <p role="status" className="text-meta text-state-danger">
            {listError}
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-2">
        {visible.length === 0 ? (
          <div className="px-3 py-6">
            <p className="text-body text-secondary">这里还没有任务</p>
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
            只删除「{pendingDelete?.label}」的 {pendingDelete?.count} 条任务历史
            （记录 + checkpoint + trace），<strong>不会删除磁盘文件</strong>；
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
