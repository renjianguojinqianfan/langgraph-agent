import { useEffect, useState } from "react";
import {
  apiErrorText,
  createTask,
  getTask,
  putMode,
  putTaskModel,
  sendMessage,
} from "../../api/client";
import { shouldSendOnEnter } from "../../lib/keys";
import { isParked, isStoppable } from "../../lib/taskState";
import { profileName, useModelStore } from "../../store/modelStore";
import { useTaskStore } from "../../store/taskStore";
import { TaskMode } from "../../types";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Segmented } from "../ui/Segmented";

const MODE_OPTIONS: Array<{ value: TaskMode; label: string; title: string }> = [
  { value: "plan", label: "计划", title: "只读工具；产出可改的计划" },
  {
    value: "build",
    label: "执行",
    title: "全部已挂载工具；危险动作由规则表当场拒绝",
  },
];

export function Composer() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  // Mode for a task that does not exist yet; an existing task carries its own.
  const [draftMode, setDraftMode] = useState<TaskMode>("build");
  const [draftModel, setDraftModel] = useState("");
  const [switchNote, setSwitchNote] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const task = useTaskStore((s) =>
    s.currentTaskId ? s.detail[s.currentTaskId] : undefined
  );
  const setCurrent = useTaskStore((s) => s.setCurrentTask);
  const upsert = useTaskStore((s) => s.upsertTask);
  const workspaceDir = useTaskStore((s) => s.workspaceDir);
  const models = useModelStore((s) => s.models);
  const defaultModelId = useModelStore((s) => s.defaultId);

  // A server reason belongs to the task that produced it; switching tasks must
  // not carry "操作失败" into the next conversation.
  useEffect(() => {
    setSwitchNote(null);
    setSendError(null);
  }, [currentTaskId]);

  // B1 (#219): a failed task is parked too — its checkpoint is intact, so the
  // next message continues the same conversation (resume() stays INTERRUPTED).
  const parked = task ? isParked(task.status) : false;
  const failed = task?.status === "FAILED";
  const running = task ? isStoppable(task.status) : false;
  // A selected task whose snapshot has not arrived yet must not be treated as
  // "new task": sending would silently create a second task.
  const loading = !!currentTaskId && !task;
  const inputDisabled = loading || (!!task && !parked);
  const canSend = !busy && !inputDisabled && !!text.trim();
  const mode: TaskMode = task?.mode ?? draftMode;
  const selectedModel = task
    ? task.model || defaultModelId
    : draftModel || defaultModelId;
  const modelLabel = profileName(models, selectedModel);
  const hasSelectedOption = models.some((m) => m.id === selectedModel);

  // One reason line under the input; the placeholder only carries the action
  // (new task / continue) and never repeats the disable reason.
  const disabledReason = loading
    ? "正在加载任务…"
    : task?.status === "PENDING"
      ? "任务启动中，稍等片刻…"
      : task?.status === "RUNNING"
        ? "任务运行中；停下或等这一轮结束再发"
        : null;
  const bottomNote = disabledReason ?? sendError;

  const onMode = async (next: TaskMode) => {
    setSwitchNote(null);
    if (!task) {
      setDraftMode(next);
      return;
    }
    try {
      const r = await putMode(task.id, next);
      if (r.data?.ok) {
        const fresh = await getTask(task.id);
        if (fresh.data) upsert(fresh.data);
        if (running) setSwitchNote("下一轮生效");
      } else {
        setSwitchNote(apiErrorText(r));
      }
    } catch {
      setSwitchNote("切换失败");
    }
  };

  const onModel = async (next: string) => {
    setSwitchNote(null);
    if (!task) {
      setDraftModel(next);
      return;
    }
    if (next === (task.model || defaultModelId)) return;
    try {
      const r = await putTaskModel(task.id, next);
      if (r.data?.ok) {
        const fresh = await getTask(task.id);
        if (fresh.data) upsert(fresh.data);
        if (running) setSwitchNote("下一轮生效");
      } else {
        setSwitchNote(apiErrorText(r));
      }
    } catch {
      setSwitchNote("切换失败");
    }
  };

  const onSubmit = async () => {
    const input = text.trim();
    if (!input || busy || inputDisabled) return;
    setBusy(true);
    setSendError(null);
    try {
      if (!task) {
        const res = await createTask({
          input,
          mode: draftMode,
          workspace: workspaceDir || undefined,
          model: draftModel || defaultModelId || undefined,
        });
        const id = res.data?.task_id;
        if (id) {
          setCurrent(id);
          // Only clear what was sent: a draft typed while the request was in
          // flight must survive.
          setText((prev) => (prev.trim() === input ? "" : prev));
        } else {
          setSendError(apiErrorText(res));
        }
      } else if (parked) {
        // #139: one task = one conversation — speaking to a parked task
        // continues it in place (same thread_id / checkpoint). A 409 (no
        // checkpoint, still shutting down, …) must not eat the message: keep
        // the text and show the server's reason.
        const res = await sendMessage(task.id, input);
        if (res.data?.task) {
          upsert(res.data.task);
          setText((prev) => (prev.trim() === input ? "" : prev));
        } else {
          setSendError(apiErrorText(res));
        }
      }
    } catch {
      setSendError("发送失败");
    } finally {
      setBusy(false);
    }
  };

  const placeholder = loading
    ? "…"
    : !task
      ? "用自然语言描述一个任务，例如：联网查资料并写一份报告…"
      : failed
        ? "任务出错，接着说一句继续…"
        : parked
          ? "接着说一句，任务从这里继续…"
          : "…";

  return (
    <div className="shrink-0 border-t border-rule px-4 py-3">
      <div className="mb-2 flex min-w-0 items-center gap-2">
        <Segmented
          options={MODE_OPTIONS}
          value={mode}
          onChange={onMode}
          label="模式"
        />
        <select
          aria-label="模型"
          title={modelLabel}
          value={selectedModel}
          disabled={loading || busy || models.length === 0}
          onChange={(e) => onModel(e.target.value)}
          className="min-w-0 max-w-[220px] rounded-control border border-rule-strong bg-surface-well px-2 py-1 text-meta text-secondary disabled:opacity-50"
        >
          {!hasSelectedOption && (
            <option value={selectedModel}>
              {selectedModel ? selectedModel : "默认"}
            </option>
          )}
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
        {switchNote && (
          <span
            className="min-w-0 truncate text-meta text-tertiary"
            title={switchNote}
          >
            {switchNote}
          </span>
        )}
      </div>
      <div className="flex items-end gap-2">
        <textarea
          aria-label="任务输入"
          className="min-h-[64px] flex-1 resize-none overscroll-contain rounded-composer border border-rule-strong bg-surface-well px-3 py-2.5 text-body text-primary placeholder:text-tertiary focus-visible:border-signal-now disabled:opacity-50"
          rows={2}
          placeholder={placeholder}
          aria-describedby={bottomNote ? "composer-reason" : undefined}
          value={text}
          disabled={inputDisabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (
              shouldSendOnEnter(
                e.key,
                e.shiftKey,
                e.nativeEvent.isComposing
              )
            ) {
              e.preventDefault();
              void onSubmit();
            }
          }}
        />
        <Button
          variant="primary"
          size="md"
          className="h-9 w-9 shrink-0 p-0"
          aria-label="发送"
          onClick={onSubmit}
          disabled={!canSend}
        >
          <Icon name="send" />
        </Button>
      </div>
      <div className="mt-2 flex items-center gap-2 text-meta">
        <span
          id="composer-reason"
          className="min-w-0 flex-1 truncate text-tertiary"
          title={bottomNote ?? undefined}
        >
          {bottomNote}
        </span>
        <span className="shrink-0 text-tertiary">
          Enter 发送 · Shift+Enter 换行
        </span>
      </div>
    </div>
  );
}
