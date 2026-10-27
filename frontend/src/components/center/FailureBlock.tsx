import { useTaskStore } from "../../store/taskStore";
import type { Task } from "../../types";
import { Button } from "../ui/Button";

export function FailureBlock({ task }: { task: Task }) {
  const setCurrentTask = useTaskStore((s) => s.setCurrentTask);
  if (task.status !== "FAILED" || !task.error) return null;

  return (
    <div
      role="alert"
      className="rounded-sheet border border-state-danger/30 bg-state-danger/5 px-4 py-3"
    >
      <div className="text-body text-state-danger">任务失败：{task.error}</div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-meta text-tertiary">
        <span>可以接着说一句继续，或新建任务重来。</span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setCurrentTask(null)}
        >
          新建任务
        </Button>
      </div>
    </div>
  );
}
