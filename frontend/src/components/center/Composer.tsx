import { useEffect, useRef, useState } from "react";
import { apiErrorText, stopTask } from "../../api/client";
import { useComposerControls } from "../../hooks/useComposerControls";
import { useComposerSend } from "../../hooks/useComposerSend";
import { useSkillCatalog } from "../../hooks/useSkillCatalog";
import { BACKEND_UNREACHABLE } from "../../lib/copy";
import { shouldSendOnEnter } from "../../lib/keys";
import {
  customModelError,
  entryProviderId,
  hasEntry,
  modelLabel,
  selectedModelId,
  selectGroups,
} from "../../lib/modelCatalog";
import { requestTaskRail } from "../../lib/railEvents";
import { insertSkillReference, skillGroupView } from "../../lib/skillCatalog";
import {
  buildMenuEntries,
  firstEnabledIndex,
  menuEntryDescendantId,
  menuEntryEnabled,
  moveSelection,
  removeCommandToken,
  slashCommandItems,
  slashMenuState,
  type SlashCommandId,
} from "../../lib/slashCommands";
import { useModelStore } from "../../store/modelStore";
import { useTaskStore } from "../../store/taskStore";
import { TaskMode } from "../../types";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Segmented } from "../ui/Segmented";
import { ComposerHelpDialog } from "./ComposerHelpDialog";
import { SlashCommandMenu } from "./SlashCommandMenu";

/** D-32 / #232: four API paths + store writes live in the two hooks; this
 * component keeps only the structure, the key handling and the rendered
 * matrix (`composerView`). Copy, aria and disabled rules are unchanged.
 * #243 P2-3: the model control is a Provider → model native `<select>` with
 * `<optgroup>`s (D-37, no new overlay), plus the catalog-outside model row
 * (the service accepts `{provider, model}` and auto-creates a manual entry).
 * #342: a running task keeps the textarea editable for `/stop` and the next
 * draft, but `canSend` stays false (button and Enter). */
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

/** #342: one composer on the page, so the listbox id is a constant. */
const SLASH_MENU_ID = "slash-command-menu";

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
  const setCurrentTask = useTaskStore((s) => s.setCurrentTask);
  const workspaceDir = useTaskStore((s) => s.workspaceDir);
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
  // #345: the skill half of the `/` menu; independent of command execution.
  const { catalog: skillCatalog, reload: reloadSkills } = useSkillCatalog(
    currentTaskId,
    workspaceDir
  );

  // Catalog-outside model row (not part of the composerView matrix: it never
  // changes whether a message can be sent).
  const [customOpen, setCustomOpen] = useState(false);
  const [customProviderId, setCustomProviderId] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [customError, setCustomError] = useState("");
  const [applyingCustom, setApplyingCustom] = useState(false);

  // #342: `/` menu state. `caret` follows the textarea; `dismissed` lets Esc
  // close the menu without deleting the draft; `selected` is the highlight.
  const composerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const modelRef = useRef<HTMLSelectElement>(null);
  const helpRef = useRef<HTMLDialogElement>(null);
  const pendingCaretRef = useRef<number | null>(null);
  const [caret, setCaret] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [selected, setSelected] = useState(0);
  const [helpOpen, setHelpOpen] = useState(false);
  const [commandNote, setCommandNote] = useState<string | null>(null);

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
    // The old draft's caret must not reopen its `/` menu on the new task.
    setCaret(0);
    setDismissed(false);
    setSelected(0);
    setHelpOpen(false);
    setCommandNote(null);
  }, [currentTaskId, resetControls, resetSend]);

  // #342: the help dialog is a native modal (focus trap / Esc / focus return
  // already come from the platform).
  useEffect(() => {
    const dialog = helpRef.current;
    if (!dialog) return;
    if (helpOpen && !dialog.open) dialog.showModal();
    else if (!helpOpen && dialog.open) dialog.close();
  }, [helpOpen]);

  // #342: keep the caret where `removeCommandToken` left it after a command
  // rewrote the draft (typing keeps the browser's own caret).
  useEffect(() => {
    const element = textareaRef.current;
    const target = pendingCaretRef.current;
    if (!element || target === null) return;
    pendingCaretRef.current = null;
    const next = Math.min(target, element.value.length);
    element.setSelectionRange(next, next);
    setCaret(next);
  }, [text]);

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

  // #342: one derived answer for the whole menu; the component only holds the
  // highlight and the Esc-dismissed flag. #345 adds the skill group to the
  // same keyboard list (commands first, then skills, then the retry row).
  const slashContext = { task, mode, hasModels: models.length > 0 };
  const slash = slashMenuState(text, caret, slashContext);
  const slashOpen = slash.open && !dismissed;
  const slashQuery = slash.token?.query ?? "";
  const skillGroup = skillGroupView(skillCatalog, slashQuery);
  const skillItems = slashOpen ? skillGroup.items : [];
  const skillRetry = slashOpen && skillGroup.canRetry;
  // One keyboard list: commands, then skills, then the retry row.
  const menuEntries = buildMenuEntries(slash.items, skillItems, skillRetry);
  const entryCount = menuEntries.length;
  const activeIndex =
    slashOpen && entryCount > 0
      ? Math.min(Math.max(selected, 0), entryCount - 1)
      : -1;
  const activeEntry = activeIndex >= 0 ? menuEntries[activeIndex] : null;
  const activeDescendant = activeEntry
    ? menuEntryDescendantId(activeEntry, SLASH_MENU_ID)
    : undefined;
  const helpItems = slashCommandItems("", slashContext);
  const bottomNote = view.bottomNote ?? commandNote;

  // A new prefix starts from its first runnable option; availability changes
  // (task status, skill catalog arrival) keep the current highlight and clamp
  // it at render time.
  useEffect(() => {
    if (!slashOpen) return;
    setSelected(
      firstEnabledIndex(
        menuEntries.map((entry) => ({ enabled: menuEntryEnabled(entry) }))
      )
    );
  }, [slashOpen, slashQuery, skillGroup.status, skillItems.length, skillRetry]);

  const runStop = async (id: string) => {
    try {
      const response = await stopTask(id);
      setCommandNote(
        response.data?.ok ? "已请求停止当前任务" : apiErrorText(response)
      );
    } catch {
      setCommandNote(`停止失败：${BACKEND_UNREACHABLE}`);
    }
  };

  /** Execute one built-in and remove its `/token` from the draft. Never sends
   * a message; disabled options are a no-op (their reason is inline). */
  const executeCommand = (id: SlashCommandId) => {
    if (!slash.token) return;
    const item = slash.items.find((entry) => entry.id === id);
    if (!item?.enabled) return;
    const token = slash.token;
    setCommandNote(null);
    setDismissed(false);
    setText(removeCommandToken(text, token));
    pendingCaretRef.current = token.start;
    setCaret(token.start);
    setSelected(0);

    switch (id) {
      case "new":
        setText("");
        pendingCaretRef.current = 0;
        setCurrentTask(null);
        textareaRef.current?.focus();
        break;
      case "stop":
        if (task) void runStop(task.id);
        break;
      case "plan":
      case "build":
        void onMode(id);
        break;
      case "model":
        modelRef.current?.focus();
        break;
      case "resume":
        setCommandNote("输入下一条消息即可继续。");
        textareaRef.current?.focus();
        break;
      case "tasks":
        requestTaskRail();
        break;
      case "help":
        setHelpOpen(true);
        break;
    }
  };

  /** #345: insert the visible reference at the `/` token; selection keeps
   * only the name because `load_skill` reads both layers. */
  const executeSkill = (name: string) => {
    if (!slash.token) return;
    const next = insertSkillReference(text, slash.token, name);
    setCommandNote(null);
    setDismissed(false);
    setText(next.text);
    pendingCaretRef.current = next.caret;
    setCaret(next.caret);
    setSelected(0);
    textareaRef.current?.focus();
  };

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
    <div
      ref={composerRef}
      className="shrink-0 border-t border-rule px-4 py-3"
    >
      <div className="mb-2 flex min-w-0 items-center gap-2">
        <Segmented
          options={MODE_OPTIONS}
          value={mode}
          onChange={onMode}
          label="模式"
        />
        <select
          ref={modelRef}
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

      <div className="relative">
        {slashOpen && (
          <SlashCommandMenu
            id={SLASH_MENU_ID}
            commands={slash.items}
            skillGroup={skillGroup}
            activeIndex={activeIndex}
            onHighlight={setSelected}
            onRun={executeCommand}
            onPickSkill={executeSkill}
            onRetrySkills={reloadSkills}
          />
        )}
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            aria-label="任务输入"
            aria-controls={slashOpen ? SLASH_MENU_ID : undefined}
            aria-expanded={slashOpen}
            aria-activedescendant={slashOpen ? activeDescendant : undefined}
            className="min-h-[64px] flex-1 resize-none overscroll-contain rounded-composer border border-rule-strong bg-surface-well px-3 py-2.5 text-body text-primary placeholder:text-tertiary focus-visible:border-signal-now disabled:opacity-50"
            rows={2}
            placeholder={view.placeholder}
            aria-describedby={bottomNote ? "composer-reason" : undefined}
            value={text}
            disabled={view.inputDisabled}
            onChange={(e) => {
              setText(e.target.value);
              setCaret(e.target.selectionStart ?? e.target.value.length);
              setDismissed(false);
              setCommandNote(null);
            }}
            onSelect={(e) => {
              setCaret(e.currentTarget.selectionStart);
            }}
            onBlur={(e) => {
              // Keep the menu while focus stays inside the composer (the model
              // select, the send button); leaving it closes the popover.
              const next = e.relatedTarget;
              if (next instanceof Node && composerRef.current?.contains(next)) {
                return;
              }
              setDismissed(true);
            }}
            onKeyDown={(e) => {
              if (slashOpen && !e.nativeEvent.isComposing) {
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                  if (entryCount > 0) {
                    e.preventDefault();
                    setSelected((current) =>
                      moveSelection(
                        current,
                        e.key === "ArrowDown" ? 1 : -1,
                        entryCount
                      )
                    );
                    return;
                  }
                }
                if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  setDismissed(true);
                  return;
                }
                if (
                  (e.key === "Enter" || e.key === "Tab") &&
                  entryCount > 0
                ) {
                  // A match wins over send/focus traversal. A disabled command
                  // is a no-op so its reason stays readable; a no-match menu
                  // falls through and Enter sends the text normally.
                  e.preventDefault();
                  if (activeEntry?.kind === "command") {
                    if (activeEntry.enabled) executeCommand(activeEntry.id);
                  } else if (activeEntry?.kind === "skill") {
                    executeSkill(activeEntry.name);
                  } else if (activeEntry?.kind === "retry") {
                    reloadSkills();
                  }
                  return;
                }
              }
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
      </div>
      <div className="mt-2 flex items-center gap-2 text-meta">
        <span
          id="composer-reason"
          role="status"
          className="min-w-0 flex-1 truncate text-tertiary"
          title={bottomNote ?? undefined}
        >
          {bottomNote}
        </span>
        <span className="shrink-0 text-tertiary">
          Enter 发送 · Shift+Enter 换行
        </span>
      </div>

      <ComposerHelpDialog
        dialogRef={helpRef}
        items={helpItems}
        onClose={() => setHelpOpen(false)}
      />
    </div>
  );
}
