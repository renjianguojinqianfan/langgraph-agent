import { TraceTab } from "../TraceTab";
import { Button } from "../ui/Button";

/** Full-width replay face in the center column; the tab stays untouched. */
export function TraceFace({
  taskId,
  onClose,
}: {
  taskId: string;
  onClose: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-rule px-4 py-2">
        <Button variant="quiet" size="sm" onClick={onClose}>
          返回对话
        </Button>
        <span
          className="min-w-0 flex-1 truncate font-mono text-machine text-tertiary"
          title={taskId}
        >
          Trace 回放 · {taskId}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3">
        <TraceTab taskId={taskId} />
      </div>
    </div>
  );
}
