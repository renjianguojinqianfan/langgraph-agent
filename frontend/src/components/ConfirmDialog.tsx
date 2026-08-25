import { confirmTask } from "../api/client";
import { useTaskStore } from "../store/taskStore";

function pretty(v: any): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

export function ConfirmDialog() {
  const confirm = useTaskStore((s) => s.confirm);
  const close = useTaskStore((s) => s.closeConfirm);
  const task = useTaskStore((s) =>
    confirm.task_id ? s.detail[confirm.task_id] : undefined
  );

  if (!confirm.open) return null;

  // P1 item 1: when the current task has a high-risk scan report, label the
  // dialog as a risk-operation confirmation.
  const hasRisk = task?.risk_report?.some((it) => it.level === "high") ?? false;

  // P2: classify the confirmation title by tool prefix (Git / MCP / generic).
  const toolName = confirm.tool_name ?? "";
  const title = hasRisk
    ? "🛡 风险操作确认"
    : toolName.startsWith("git_")
    ? "Git 操作确认"
    : toolName.startsWith("mcp__")
    ? "MCP 工具确认"
    : "需要人工确认";

  const onDecide = async (approved: boolean) => {
    if (confirm.task_id && confirm.tool_call_id) {
      try {
        await confirmTask(confirm.task_id, {
          tool_call_id: confirm.tool_call_id,
          approved,
        });
      } catch {
        /* ignore */
      }
    }
    close();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[420px] max-w-[90vw] bg-panel border border-slate-700 rounded-lg p-4 shadow-xl">
        <div className="font-semibold text-amber-400 mb-2">{title}</div>
        <div className="text-sm mb-2">
          工具 <span className="font-mono text-sky-300">{confirm.tool_name}</span>{" "}
          属于高风险操作，是否继续执行？
        </div>
        <pre className="bg-slate-900 rounded p-2 text-xs overflow-auto max-h-48 mb-3">
          {pretty(confirm.input)}
        </pre>
        <div className="flex justify-end gap-2">
          <button
            onClick={() => onDecide(false)}
            className="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 text-sm"
          >
            拒绝
          </button>
          <button
            onClick={() => onDecide(true)}
            className="px-3 py-1.5 rounded bg-green-600 hover:bg-green-500 text-white text-sm"
          >
            允许
          </button>
        </div>
      </div>
    </div>
  );
}
