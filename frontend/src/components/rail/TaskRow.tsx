import { cx } from "../../lib/cx";
import { relativeTime } from "../../lib/format";
import type { Task } from "../../types";
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
  const title =
    (task.title || "").trim() || (task.user_input || "").trim() || "未命名任务";

  return (
    <button
      type="button"
      title={title}
      aria-current={selected ? "page" : undefined}
      onClick={() => onSelect(task.id)}
      className={cx(
        "w-full px-2.5 py-2 text-left transition-colors duration-150",
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
  );
}
