import { cx } from "../../lib/cx";
import type { SubTask } from "../../types";

const META: Record<string, { label: string; tone: string; dot: string }> = {
  pending: { label: "等待中", tone: "text-tertiary", dot: "bg-tertiary" },
  running: { label: "运行中", tone: "text-signal-now", dot: "bg-signal-now" },
  completed: { label: "已完成", tone: "text-state-ok", dot: "bg-state-ok" },
  failed: { label: "失败", tone: "text-state-danger", dot: "bg-state-danger" },
};

export function subagentMeta(status: string) {
  return (
    META[status] ?? { label: status, tone: "text-tertiary", dot: "bg-tertiary" }
  );
}

export function SubagentPlate({
  subtask,
  active,
  onToggle,
}: {
  subtask: SubTask;
  active: boolean;
  onToggle: () => void;
}) {
  const meta = subagentMeta(subtask.status);
  return (
    <button
      type="button"
      aria-expanded={active}
      onClick={onToggle}
      className={cx(
        "flex min-w-0 items-center gap-2 rounded-control border px-2 py-1 text-left transition-colors duration-150",
        active
          ? "border-rule-strong bg-surface-hover"
          : "border-rule hover:bg-surface-hover"
      )}
    >
      <span
        aria-hidden="true"
        className={cx(
          "h-1.5 w-1.5 shrink-0 rounded-full",
          meta.dot,
          subtask.status === "running" && "animate-pulse"
        )}
      />
      <span
        className="max-w-[200px] truncate text-meta text-primary"
        title={subtask.name || undefined}
      >
        {subtask.name || "未命名子任务"}
      </span>
      <span className={cx("shrink-0 text-meta", meta.tone)}>{meta.label}</span>
      <span className="shrink-0 font-mono text-machine text-tertiary">
        {subtask.subtask_id.slice(0, 8)}
      </span>
      <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
        {subtask.artifacts.length} 产物
      </span>
    </button>
  );
}
