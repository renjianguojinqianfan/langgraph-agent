import { ArtifactList } from "./ArtifactList";
import { InputBar } from "./InputBar";
import { MessageStream } from "./MessageStream";
import { RiskBanner } from "./RiskBanner";
import { StepTimeline } from "./StepTimeline";
import { SubtaskList } from "./SubtaskList";
import { TaskHeader } from "./TaskHeader";
import { TraceTab } from "./TraceTab";
import { Task, TaskViewTab } from "../types";

interface TaskPanelProps {
  task: Task | null;
  tab: TaskViewTab;
  onTabChange: (tab: TaskViewTab) => void;
  onToggleKb?: () => void;
  kbOpen?: boolean;
}

export function TaskPanel({ task, tab, onTabChange, onToggleKb, kbOpen }: TaskPanelProps) {
  return (
    <section className="flex-1 flex flex-col min-w-0">
      {task ? (
        <TaskHeader task={task} />
      ) : (
        <header className="px-4 py-3 border-b border-slate-800 font-semibold">
          新任务
        </header>
      )}

      {task && (
        <div className="flex items-center border-b border-slate-800 px-4">
          <button
            onClick={() => onTabChange("run")}
            className={`px-3 py-2 -mb-px border-b-2 text-sm transition ${
              tab === "run"
                ? "border-sky-500 text-sky-300"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            运行
          </button>
          <button
            onClick={() => onTabChange("subtask")}
            className={`px-3 py-2 -mb-px border-b-2 text-sm transition ${
              tab === "subtask"
                ? "border-sky-500 text-sky-300"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            子任务
          </button>
          <button
            onClick={() => onTabChange("trace")}
            className={`px-3 py-2 -mb-px border-b-2 text-sm transition ${
              tab === "trace"
                ? "border-sky-500 text-sky-300"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            Trace / 运行日志
          </button>
          {onToggleKb && (
            <button
              onClick={onToggleKb}
              className={`ml-auto px-3 py-2 text-sm transition ${
                kbOpen
                  ? "text-sky-300"
                  : "text-slate-500 hover:text-slate-300"
              }`}
            >
              📚 知识库
            </button>
          )}
        </div>
      )}

      <div className="flex-1 overflow-auto p-4 space-y-4">
        {task ? (
          tab === "trace" ? (
            <TraceTab taskId={task.id} />
          ) : tab === "subtask" ? (
            <>
              <div className="text-xs text-slate-400 mb-1">子任务</div>
              <SubtaskList subtasks={task.subtasks ?? []} />
            </>
          ) : (
            <>
              <RiskBanner items={task.risk_report ?? []} />
              <MessageStream task={task} />
              <div>
                <div className="text-xs text-slate-400 mb-1">步骤时间线</div>
                <StepTimeline task={task} />
              </div>
              <ArtifactList task={task} />
            </>
          )
        ) : (
          <div className="text-slate-500 text-sm">
            在下方输入任务开始，或在左侧选择历史任务。
          </div>
        )}
      </div>

      {tab === "run" && <InputBar />}
    </section>
  );
}
