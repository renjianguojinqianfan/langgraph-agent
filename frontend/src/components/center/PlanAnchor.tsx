import type { Task } from "../../types";
import { Icon } from "../ui/Icon";
import { Status } from "../ui/Status";

export function PlanAnchor({
  task,
  onOpenPlan,
}: {
  task: Task;
  onOpenPlan: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpenPlan}
      className="flex w-full items-center gap-2 rounded-control border-y border-rule px-1 py-1.5 text-left text-meta text-tertiary transition-colors duration-150 hover:bg-surface-hover hover:text-secondary"
    >
      <Icon name="file" size={14} />
      <span className="shrink-0">
        {task.plan_version > 0 ? `计划 v${task.plan_version}` : "尚无计划"}
      </span>
      <Status status={task.status} live={false} />
      <span className="ml-auto shrink-0">打开</span>
    </button>
  );
}
