import { Composer } from "./center/Composer";
import { Conversation } from "./center/Conversation";
import { SubagentStrip } from "./center/SubagentStrip";
import { TaskHeader } from "./center/TaskHeader";
import { TraceFace } from "./dash/TraceFace";
import { Task } from "../types";
import { useTaskStore } from "../store/taskStore";
import { Button } from "./ui/Button";
import { ReauthAction } from "./ReauthAction";

interface TaskPanelProps {
  task: Task | null;
  /** #256: why the selected task's snapshot could not be loaded. */
  loadError?: string;
  /** #256: the selected task's snapshot is still in flight. */
  loading?: boolean;
  rightOpen: boolean;
  onToggleRight: () => void;
  onOpenPlan: () => void;
  traceOpen: boolean;
  onCloseTrace: () => void;
}

export function TaskPanel({
  task,
  loadError = "",
  loading = false,
  rightOpen,
  onToggleRight,
  onOpenPlan,
  traceOpen,
  onCloseTrace,
}: TaskPanelProps) {
  const setCurrentTask = useTaskStore((s) => s.setCurrentTask);
  const retryDetail = useTaskStore((s) => s.retryDetail);
  // #256: a 401 fails every face; the way back is dropping the dead token.
  const authExpired = useTaskStore((s) => s.authExpired);

  if (task && traceOpen) {
    return (
      <section className="flex min-h-0 flex-1 flex-col min-w-0">
        <TaskHeader
          task={task}
          rightOpen={rightOpen}
          onToggleRight={onToggleRight}
        />
        <TraceFace taskId={task.id} onClose={onCloseTrace} />
      </section>
    );
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col min-w-0">
      {task ? (
        <TaskHeader
          task={task}
          rightOpen={rightOpen}
          onToggleRight={onToggleRight}
        />
      ) : loadError ? (
        // #256: the snapshot never arrived — this is a failed load, not a new
        // task. Saying "新任务" here invited a second task on the next send.
        <header className="flex h-[52px] shrink-0 items-center border-b border-rule px-4 max-[860px]:pl-12">
          <h1 className="text-title text-primary">任务加载失败</h1>
        </header>
      ) : loading ? (
        // #256: in flight is its own face — neither "新任务" nor an error.
        <header className="flex h-[52px] shrink-0 items-center border-b border-rule px-4 max-[860px]:pl-12">
          <h1 className="text-title text-tertiary">加载中…</h1>
        </header>
      ) : (
        <header className="flex h-[52px] shrink-0 items-center border-b border-rule px-4 max-[860px]:pl-12">
          <h1 className="text-title text-primary">新任务</h1>
        </header>
      )}

      {/* #256 (评审 AC2): the snapshot failed while a partial task is already
          on screen (SSE replay / a list row seeded it). The error face must
          not be hidden by the task — the composer stays disabled and points
          at entries that have to exist, so the banner carries them. */}
      {task && loadError && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 border-b border-rule px-4 py-2"
        >
          <span className="min-w-0 flex-1 text-meta text-state-danger">
            {loadError}
          </span>
          {authExpired ? (
            <ReauthAction />
          ) : (
            <Button variant="outline" size="sm" onClick={retryDetail}>
              重试
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCurrentTask(null)}
          >
            新建任务
          </Button>
        </div>
      )}

      {task && task.mode === "build" && (
        <SubagentStrip subtasks={task.subtasks ?? []} />
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        {task ? (
          <Conversation task={task} onOpenPlan={onOpenPlan} />
        ) : loadError ? (
          <div className="flex flex-1 flex-col items-start justify-center gap-3 px-6">
            <p role="alert" className="text-body text-state-danger">
              {loadError}
            </p>
            <p className="text-meta text-tertiary">
              {authExpired
                ? "重新登录后会回到这里。"
                : "可以在左侧重新选择，或重试这一次加载。"}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {authExpired ? (
                <ReauthAction />
              ) : (
                <Button variant="outline" size="sm" onClick={retryDetail}>
                  重试
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCurrentTask(null)}
              >
                新建任务
              </Button>
            </div>
          </div>
        ) : loading ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4">
            <p className="text-body text-tertiary">正在加载任务…</p>
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4">
            <p className="text-invite text-primary">
              想在这个目录做点什么？
            </p>
            <p className="text-body text-tertiary">
              在下方描述任务，或在左侧选择历史任务。
            </p>
          </div>
        )}
      </div>

      <Composer />
    </section>
  );
}
