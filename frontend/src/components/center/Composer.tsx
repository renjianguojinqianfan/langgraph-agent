import { useEffect, useState } from "react";
import { useComposerControls } from "../../hooks/useComposerControls";
import { useComposerSend } from "../../hooks/useComposerSend";
import { shouldSendOnEnter } from "../../lib/keys";
import {
  customModelError,
  entryProviderId,
  hasEntry,
  modelLabel,
  selectedModelId,
  selectGroups,
} from "../../lib/modelCatalog";
import { useModelStore } from "../../store/modelStore";
import { useTaskStore } from "../../store/taskStore";
import { TaskMode } from "../../types";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Segmented } from "../ui/Segmented";

/** D-32 / #232: four API paths + store writes live in the two hooks; this
 * component keeps only the structure, the key handling and the rendered
 * matrix (`composerView`). Copy, aria and disabled rules are unchanged.
 * #243 P2-3: the model control is a Provider → model native `<select>` with
 * `<optgroup>`s (D-37, no new overlay), plus the catalog-outside model row
 * (the service accepts `{provider, model}` and auto-creates a manual entry). */
const MODE_OPTIONS: Array<{ value: TaskMode; label: string; title: string }> = [
  { value: "plan", label: "计划", title: "只读工具；产出可改的计划" },
  {
    value: "build",
    label: "执行",
    title: "全部已挂载工具；危险动作由规则表当场拒绝",
  },
];

/** Sentinel option: opens the catalog-outside model row instead of a switch. */
const CUSTOM_MODEL_OPTION = "__custom_model__";

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
  const providers = useModelStore((s) => s.providers);
  const models = useModelStore((s) => s.models);
  const defaultModelId = useModelStore((s) => s.defaultId);

  const {
    draftMode,
    draftModel,
    switchNote,
    reset: resetControls,
    onMode,
    onModel,
    onCustomModel,
  } = useComposerControls({ task });
  const {
    text,
    setText,
    busy,
    reset: resetSend,
    onSubmit,
    view,
  } = useComposerSend({
    task,
    currentTaskId,
    detailError,
    detailAuthExpired,
    draftMode,
    draftModel,
    defaultModelId,
  });

  // Catalog-outside model row (not part of the composerView matrix: it never
  // changes whether a message can be sent).
  const [customOpen, setCustomOpen] = useState(false);
  const [customProviderId, setCustomProviderId] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [customError, setCustomError] = useState("");
  const [applyingCustom, setApplyingCustom] = useState(false);

  // A server reason belongs to the task that produced it; switching tasks must
  // not carry "操作失败" into the next conversation.
  useEffect(() => {
    resetControls();
    resetSend();
    setCustomOpen(false);
    setCustomProviderId("");
    setCustomModel("");
    setCustomError("");
    setApplyingCustom(false);
  }, [currentTaskId, resetControls, resetSend]);

  const mode: TaskMode = task?.mode ?? draftMode;
  const groups = selectGroups(providers, models);
  const selectedModel = selectedModelId(
    !!task,
    task?.model ?? "",
    draftModel,
    defaultModelId
  );
  const hasSelectedOption = hasEntry(models, selectedModel);
  const currentLabel = modelLabel(models, selectedModel);
  const selectedProviderId = entryProviderId(models, selectedModel);
  const effectiveProviderId =
    customProviderId || selectedProviderId || providers[0]?.id || "";

  const pickModel = (value: string) => {
    if (value === CUSTOM_MODEL_OPTION) {
      setCustomOpen(true);
      setCustomError("");
      return;
    }
    setCustomOpen(false);
    setCustomError("");
    void onModel(value);
  };

  const applyCustom = async () => {
    // One in-flight switch at a time: a double click (or Enter held) would
    // otherwise POST twice, and the second answer (409) would overwrite the
    // success note with a failure (#299 LOW-5).
    if (applyingCustom) return;
    const error = customModelError(effectiveProviderId, customModel);
    setCustomError(error);
    if (error) return;
    setApplyingCustom(true);
    try {
      const ok = await onCustomModel(effectiveProviderId, customModel);
      if (ok) {
        setCustomOpen(false);
        setCustomModel("");
        setCustomError("");
      }
    } finally {
      setApplyingCustom(false);
    }
  };

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
          title={currentLabel}
          value={customOpen ? CUSTOM_MODEL_OPTION : selectedModel}
          disabled={view.loading || busy || providers.length === 0}
          onChange={(e) => pickModel(e.target.value)}
          className="min-w-0 max-w-[240px] rounded-control border border-rule-strong bg-surface-well px-2 py-1 text-meta text-secondary disabled:opacity-50"
        >
          {!customOpen && !hasSelectedOption && (
            <option value={selectedModel}>
              {selectedModel ? currentLabel : "默认"}
            </option>
          )}
          {groups.map((group) => (
            <optgroup key={group.providerId} label={group.providerName}>
              {group.models.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </optgroup>
          ))}
          <option value={CUSTOM_MODEL_OPTION}>目录外模型…</option>
        </select>
        {switchNote && (
          <span
            role="status"
            aria-atomic="true"
            className="min-w-0 truncate text-meta text-tertiary"
            title={switchNote}
          >
            {switchNote}
          </span>
        )}
      </div>

      {customOpen && (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-meta">
          <select
            aria-label="目录外模型的 Provider"
            value={effectiveProviderId}
            onChange={(e) => setCustomProviderId(e.target.value)}
            className="max-w-[180px] min-w-0 truncate rounded-control border border-rule-strong bg-surface-well px-2 py-1 text-meta text-secondary"
          >
            {providers.map((provider) => (
              <option key={provider.id} value={provider.id}>
                {provider.name || provider.id}
              </option>
            ))}
          </select>
          <input
            aria-label="目录外模型 ID"
            value={customModel}
            placeholder="例如 qwen-max"
            onChange={(e) => setCustomModel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void applyCustom();
              }
            }}
            className="h-7 w-48 rounded-control border border-rule-strong bg-surface-well px-2 font-mono text-machine text-primary placeholder:text-tertiary"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={applyCustom}
            disabled={applyingCustom || !customModel.trim() || !effectiveProviderId}
            title={applyingCustom ? "切换中…" : undefined}
          >
            {applyingCustom ? "使用中…" : "使用"}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setCustomOpen(false);
              setCustomError("");
            }}
          >
            取消
          </Button>
          <span className="min-w-0 truncate text-tertiary">
            目录外 ID 在切换或新建时自动加入目录
          </span>
          {customError && (
            <span role="alert" className="text-state-danger">
              {customError}
            </span>
          )}
        </div>
      )}

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
