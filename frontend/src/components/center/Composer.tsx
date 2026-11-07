import { useEffect } from "react";
import { useComposerControls } from "../../hooks/useComposerControls";
import { useComposerSend } from "../../hooks/useComposerSend";
import { composerView } from "../../lib/composerState";
import { shouldSendOnEnter } from "../../lib/keys";
import { profileName, useModelStore } from "../../store/modelStore";
import { useTaskStore } from "../../store/taskStore";
import { TaskMode } from "../../types";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Segmented } from "../ui/Segmented";

/** D-32 / #232: four API paths + store writes live in the two hooks; this
 * component keeps only the structure, the key handling and the rendered
 * matrix (`composerView`). Copy, aria and disabled rules are unchanged. */
const MODE_OPTIONS: Array<{ value: TaskMode; label: string; title: string }> = [
  { value: "plan", label: "计划", title: "只读工具；产出可改的计划" },
  {
    value: "build",
    label: "执行",
    title: "全部已挂载工具；危险动作由规则表当场拒绝",
  },
];

export function Composer() {
  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const task = useTaskStore((s) =>
    s.currentTaskId ? s.detail[s.currentTaskId] : undefined
  );
  // #256: a selected task whose snapshot failed must not accept a send —
  // with no `task` in hand the send branch would create a second task.
  const detailError = useTaskStore((s) =>
    s.currentTaskId ? (s.detailError[s.currentTaskId] ?? "") : ""
  );
  // #256: a 401 on the detail read; the entries offered are re-login ones.
  const detailAuthExpired = useTaskStore((s) => s.authExpired.detail);
  const models = useModelStore((s) => s.models);
  const defaultModelId = useModelStore((s) => s.defaultId);

  const {
    draftMode,
    draftModel,
    switchNote,
    reset: resetControls,
    onMode,
    onModel,
  } = useComposerControls({ task });
  const {
    text,
    setText,
    busy,
    sendError,
    reset: resetSend,
    onSubmit,
  } = useComposerSend({
    task,
    currentTaskId,
    detailError,
    detailAuthExpired,
    draftMode,
    draftModel,
    defaultModelId,
  });

  // A server reason belongs to the task that produced it; switching tasks must
  // not carry "操作失败" into the next conversation.
  useEffect(() => {
    resetControls();
    resetSend();
  }, [currentTaskId, resetControls, resetSend]);

  const view = composerView({
    task,
    currentTaskId,
    detailError,
    detailAuthExpired,
    text,
    busy,
    sendError,
  });
  const mode: TaskMode = task?.mode ?? draftMode;
  const selectedModel = task
    ? task.model || defaultModelId
    : draftModel || defaultModelId;
  const modelLabel = profileName(models, selectedModel);
  const hasSelectedOption = models.some((m) => m.id === selectedModel);

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
          disabled={view.loading || busy || models.length === 0}
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
          placeholder={view.placeholder}
          aria-describedby={view.bottomNote ? "composer-reason" : undefined}
          value={text}
          disabled={view.inputDisabled}
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
          disabled={!view.canSend}
        >
          <Icon name="send" />
        </Button>
      </div>
      <div className="mt-2 flex items-center gap-2 text-meta">
        <span
          id="composer-reason"
          role="status"
          className="min-w-0 flex-1 truncate text-tertiary"
          title={view.bottomNote ?? undefined}
        >
          {view.bottomNote}
        </span>
        <span className="shrink-0 text-tertiary">
          Enter 发送 · Shift+Enter 换行
        </span>
      </div>
    </div>
  );
}
