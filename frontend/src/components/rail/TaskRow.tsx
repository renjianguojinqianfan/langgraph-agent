import { useState } from "react";
import { apiErrorText, deleteTask } from "../../api/client";
import { cx } from "../../lib/cx";
import { relativeTime } from "../../lib/format";
import { useTaskStore } from "../../store/taskStore";
import type { Task } from "../../types";
import { Button, IconButton } from "../ui/Button";
import { Status } from "../ui/Status";
import { workspaceName } from "./group";

interface TaskRowProps {
  task: Task;
  selected: boolean;
  /** "全部" rows carry the directory name; "本目录" rows do not need it. */
  showWorkspace: boolean;
  onSelect: (id: string) => void;
}

export function TaskRow({ task, selected, showWorkspace, onSelect }: TaskRowProps) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const removeTask = useTaskStore((s) => s.removeTask);
  const title =
    (task.title || "").trim() || (task.user_input || "").trim() || "未命名任务";

  const remove = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await deleteTask(task.id);
      if (response.data?.ok) {
        removeTask(task.id);
        setArmed(false);
      } else {
        setError(apiErrorText(response));
      }
    } catch {
      setError("删除失败：无法连接后台。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="group flex items-start gap-1">
        <button
          type="button"
          title={title}
          aria-current={selected ? "page" : undefined}
          onClick={() => onSelect(task.id)}
          className={cx(
            "min-w-0 flex-1 px-2.5 py-2 text-left transition-colors duration-150",
            selected
              ? "text-primary shadow-[inset_2px_0_0_var(--color-primary)]"
              : "text-secondary hover:bg-surface-hover hover:text-primary"
          )}
        >
          <div className="truncate text-body leading-snug">{title}</div>
          <div className="mt-1 flex items-center gap-2">
            <Status status={task.status} live={false} />
            <time
              dateTime={task.created_at}
              title={task.created_at}
              className="font-mono text-machine text-tertiary tabular-nums"
            >
              {relativeTime(task.created_at, Date.now())}
            </time>
          </div>
          {showWorkspace && (
            <div
              className="mt-0.5 truncate font-mono text-machine text-tertiary"
              title={task.workspace || "默认目录"}
            >
              {workspaceName(task.workspace)}
            </div>
          )}
        </button>
        <IconButton
          size="sm"
          icon="trash"
          aria-label={`删除任务：${title}`}
          title="删除历史（不动磁盘文件）"
          onClick={() => setArmed(true)}
          className="mt-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
        />
      </div>
      {armed && (
        <div className="mx-1 mt-1 flex items-center gap-2 rounded-control border border-rule bg-surface-well px-2 py-1">
          <span className="min-w-0 flex-1 truncate text-meta text-secondary">
            只删历史记录？
          </span>
          <Button size="sm" variant="danger" onClick={remove} disabled={busy}>
            {busy ? "删除中…" : "删除"}
          </Button>
          <Button size="sm" onClick={() => setArmed(false)}>
            取消
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="px-2.5 pt-1 text-meta text-state-danger">
          {error}
        </p>
      )}
    </div>
  );
}
