import { useTaskStore } from "../store/taskStore";
import { StepRecord, Task } from "../types";

const TOOL_STATUS_STYLE: Record<string, string> = {
  pending: "text-slate-400",
  success: "text-green-400",
  failed: "text-red-400",
  skipped: "text-amber-400",
};

export function StepTimeline({ task }: { task: Task }) {
  const selectedStep = useTaskStore((s) => s.selectedStep);
  const setSelectedStep = useTaskStore((s) => s.setSelectedStep);

  if (task.steps.length === 0) {
    return (
      <div className="text-sm text-slate-500">
        {task.status === "RUNNING" ? "执行中…" : "暂无步骤"}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {task.steps.map((step: StepRecord) => {
        const active = selectedStep === step.index;
        const tools = step.tool_calls
          .map((tc) => `${tc.tool_name}`)
          .join(", ");
        return (
          <button
            key={step.index}
            onClick={() => setSelectedStep(active ? null : step.index)}
            className={`w-full text-left rounded border px-3 py-2 text-sm transition ${
              active
                ? "border-sky-500 bg-sky-500/10"
                : "border-slate-800 hover:border-slate-600"
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-slate-400">
                #{step.index}
              </span>
              <span className="flex-1 truncate">
                {step.thought || tools || "step"}
              </span>
              {step.status === "running" && (
                <span className="text-xs text-blue-400 animate-pulse">运行中</span>
              )}
            </div>
            {step.tool_calls.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {step.tool_calls.map((tc) => (
                  <span
                    key={tc.id}
                    className={`text-[11px] px-1.5 py-0.5 rounded bg-slate-800 flex items-center gap-1 ${
                      TOOL_STATUS_STYLE[tc.status] || "text-slate-300"
                    }`}
                  >
                    <span>
                      {tc.tool_name}:{tc.status}
                    </span>
                    {tc.circuit_open && (
                      <span className="text-red-400 font-medium">⚡熔断</span>
                    )}
                    {(tc.retries ?? 0) > 0 && (
                      <span className="text-amber-400 font-medium">
                        ↻重试×{tc.retries}
                      </span>
                    )}
                  </span>
                ))}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
