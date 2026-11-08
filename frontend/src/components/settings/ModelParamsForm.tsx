import { useEffect, useState } from "react";
import { apiErrorText, updateProviderModel } from "../../api/client";
import { BACKEND_UNREACHABLE } from "../../lib/copy";
import {
  draftFromEntry,
  modelParamPatch,
  paramInheritHints,
  type ModelParamDraft,
  type ModelParamErrors,
  type ReasoningDraft,
  type ThinkingDraft,
} from "../../lib/modelParams";
import { PROTOCOL_ANTHROPIC } from "../../lib/modelCatalog";
import type { ModelEntry, Provider } from "../../types";
import { Button } from "../ui/Button";

interface ModelParamsFormProps {
  provider: Provider;
  /** The catalog entry as stored (raw overrides; null = inherit). */
  entry: ModelEntry;
  /** Reload the shared store after a save. */
  onRefresh: () => Promise<void>;
  onClose: () => void;
}

type Note = { kind: "ok" | "error"; text: string } | null;

const THINKING_OPTIONS: ReadonlyArray<{ value: ThinkingDraft; label: string }> = [
  { value: "inherit", label: "继承 Provider 默认" },
  { value: "on", label: "开" },
  { value: "off", label: "关" },
];

const EFFORT_OPTIONS: ReadonlyArray<{ value: ReasoningDraft; label: string }> = [
  { value: "inherit", label: "继承 Provider 默认" },
  { value: "low", label: "low" },
  { value: "medium", label: "medium" },
  { value: "high", label: "high" },
];

function isThinkingDraft(value: string): value is ThinkingDraft {
  return value === "inherit" || value === "on" || value === "off";
}

function isReasoningDraft(value: string): value is ReasoningDraft {
  return (
    value === "inherit" ||
    value === "low" ||
    value === "medium" ||
    value === "high"
  );
}

/**
 * #267 / D-36: entry-level parameter editor (temperature / max_tokens /
 * thinking / reasoning_effort).
 *
 * Saves only the changed fields through
 * `PUT /providers/{id}/models/{model_id}`; emptying a numeric field sends an
 * explicit null, which clears the entry override and inherits the provider
 * default. The protocol-specific combination rules (Anthropic rejects
 * thinking / reasoning_effort) are NOT pre-blocked: the service owns that 400
 * and its reason is shown verbatim.
 */
export function ModelParamsForm({
  provider,
  entry,
  onRefresh,
  onClose,
}: ModelParamsFormProps) {
  const [draft, setDraft] = useState<ModelParamDraft>(() => draftFromEntry(entry));
  const [errors, setErrors] = useState<ModelParamErrors>({});
  const [note, setNote] = useState<Note>(null);
  const [saving, setSaving] = useState(false);

  // Fresh form when the card/entry identity or the protocol changes; a store
  // refresh after save keeps the draft (the user's typed values are canonical
  // enough) so the success note survives.
  useEffect(() => {
    setDraft(draftFromEntry(entry));
    setErrors({});
    setNote(null);
    setSaving(false);
  }, [entry.id, provider.id, provider.protocol]);

  const hints = paramInheritHints(provider, provider.protocol);
  const anthropicNote = provider.protocol === PROTOCOL_ANTHROPIC;

  const setField = <K extends keyof ModelParamDraft>(
    field: K,
    value: ModelParamDraft[K]
  ) => {
    setDraft((prev) => ({ ...prev, [field]: value }));
    setErrors((prev) => ({ ...prev, [field]: undefined }));
  };

  const save = async () => {
    if (saving) return;
    const result = modelParamPatch(entry, draft, provider.protocol);
    setErrors(result.errors);
    if (Object.keys(result.errors).length > 0) {
      setNote({ kind: "error", text: "请先修正标红的字段。" });
      return;
    }
    if (!result.changed) {
      setNote({ kind: "ok", text: "没有改动（未填的字段都继承 Provider 默认）。" });
      return;
    }
    setSaving(true);
    setNote(null);
    try {
      const answer = await updateProviderModel(provider.id, entry.id, result.patch);
      if (answer.data) {
        await onRefresh();
        setNote({
          kind: "ok",
          text: "参数已保存；未设置 / 清空的字段继承 Provider 默认。",
        });
      } else {
        // Verbatim service reason (e.g. Anthropic + thinking 400 already names
        // the next step); never swallowed into a generic failure.
        setNote({ kind: "error", text: apiErrorText(answer) });
      }
    } catch {
      setNote({ kind: "error", text: "保存失败：" + BACKEND_UNREACHABLE });
    } finally {
      setSaving(false);
    }
  };

  const fieldError = (field: keyof ModelParamErrors) => errors[field] ?? "";

  return (
    <div className="flex flex-col gap-2 rounded-well border border-rule bg-surface-sheet p-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-meta font-semibold text-secondary">参数</span>
        <span className="min-w-0 truncate font-mono text-machine text-tertiary" title={entry.model}>
          {entry.model}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="flex flex-col gap-0.5 text-meta text-secondary">
          <span>温度（temperature）</span>
          <input
            aria-label={`温度 temperature：${entry.model}`}
            value={draft.temperature}
            placeholder={hints.temperature}
            aria-invalid={fieldError("temperature") ? true : undefined}
            onChange={(event) => setField("temperature", event.target.value)}
            className="h-7 rounded-control border border-rule bg-surface-well px-2 text-meta text-primary placeholder:text-tertiary"
          />
          <span className="text-machine text-tertiary">{hints.temperature}</span>
          {fieldError("temperature") && (
            <span role="alert" className="text-meta text-state-danger">
              {fieldError("temperature")}
            </span>
          )}
        </label>

        <label className="flex flex-col gap-0.5 text-meta text-secondary">
          <span>最大 token（max_tokens）</span>
          <input
            aria-label={`最大 token max_tokens：${entry.model}`}
            value={draft.max_tokens}
            placeholder={hints.max_tokens}
            aria-invalid={fieldError("max_tokens") ? true : undefined}
            onChange={(event) => setField("max_tokens", event.target.value)}
            className="h-7 rounded-control border border-rule bg-surface-well px-2 text-meta text-primary placeholder:text-tertiary"
          />
          <span className="text-machine text-tertiary">{hints.max_tokens}</span>
          {fieldError("max_tokens") && (
            <span role="alert" className="text-meta text-state-danger">
              {fieldError("max_tokens")}
            </span>
          )}
        </label>

        <label className="flex flex-col gap-0.5 text-meta text-secondary">
          <span>思考（thinking）</span>
          <select
            aria-label={`思考 thinking：${entry.model}`}
            value={draft.thinking}
            onChange={(event) => {
              if (isThinkingDraft(event.target.value)) {
                setField("thinking", event.target.value);
              }
            }}
            className="h-7 rounded-control border border-rule bg-surface-well px-2 text-meta text-primary"
          >
            {THINKING_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <span className="text-machine text-tertiary">{hints.thinking}</span>
        </label>

        <label className="flex flex-col gap-0.5 text-meta text-secondary">
          <span>推理强度（reasoning_effort）</span>
          <select
            aria-label={`推理强度 reasoning_effort：${entry.model}`}
            value={draft.reasoning_effort}
            onChange={(event) => {
              if (isReasoningDraft(event.target.value)) {
                setField("reasoning_effort", event.target.value);
              }
            }}
            className="h-7 rounded-control border border-rule bg-surface-well px-2 text-meta text-primary"
          >
            {EFFORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <span className="text-machine text-tertiary">{hints.reasoning_effort}</span>
        </label>
      </div>

      <p className="text-meta text-tertiary">
        只提交改动的字段；清空数值 = 传 null（回到继承）。本片只编辑条目级参数，Provider
        级默认见上方说明。
      </p>
      {anthropicNote && (
        <p className="text-meta text-tertiary">
          Anthropic 暂不支持 thinking / reasoning_effort；保存时由服务端拒绝并原样显示原因。
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={save} disabled={saving}>
          {saving ? "保存中…" : "保存参数"}
        </Button>
        <Button size="sm" onClick={onClose} disabled={saving}>
          收起
        </Button>
        {note && (
          <span
            role={note.kind === "error" ? "alert" : "status"}
            className={
              note.kind === "error"
                ? "min-w-0 flex-1 text-meta text-state-danger"
                : "min-w-0 flex-1 text-meta text-secondary"
            }
          >
            {note.text}
          </span>
        )}
      </div>
    </div>
  );
}
