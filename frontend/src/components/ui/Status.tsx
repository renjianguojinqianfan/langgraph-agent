import { TaskStatus } from "../../types";
import { cx } from "../../lib/cx";

const META: Record<
  TaskStatus,
  { label: string; tone: string; dot: string }
> = {
  PENDING: { label: "启动中", tone: "text-tertiary", dot: "bg-tertiary" },
  RUNNING: { label: "运行中", tone: "text-signal-now", dot: "bg-signal-now" },
  INTERRUPTED: {
    label: "已停下",
    tone: "text-state-warn",
    dot: "bg-state-warn",
  },
  FAILED: { label: "失败", tone: "text-state-danger", dot: "bg-state-danger" },
};

interface StatusProps {
  status: TaskStatus;
  /** 历史列表等静态上下文传 false：不给每一行都挂一个 polite live region。 */
  live?: boolean;
}

export function Status({ status, live = true }: StatusProps) {
  const meta =
    META[status] ??
    ({ label: status, tone: "text-tertiary", dot: "bg-tertiary" } as const);

  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap text-meta",
        meta.tone
      )}
      {...(live ? { role: "status", "aria-live": "polite" } : {})}
    >
      <span
        aria-hidden="true"
        className={cx(
          "inline-block h-1.5 w-1.5 rounded-full",
          meta.dot,
          status === "RUNNING" && "animate-pulse"
        )}
      />
      {meta.label}
    </span>
  );
}
