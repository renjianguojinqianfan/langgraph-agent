import type {
  ModelEntry,
  ModelParamsUpdate,
  Provider,
  ReasoningEffort,
} from "../types";
import { PROTOCOL_ANTHROPIC, protocolLabel } from "./modelCatalog";

/**
 * #267 / D-36: pure derivation behind the entry-level parameter form.
 *
 * Write semantics (backend PUT /providers/{id}/models/{model_id}):
 * - a field omitted from the body is unchanged;
 * - an explicit null clears the entry override (falls back to the provider
 *   default / client default).
 * The form therefore starts from the entry's *raw* overrides and only emits
 * the fields that actually changed.
 */

export type ThinkingDraft = "inherit" | "on" | "off";
export type ReasoningDraft = "inherit" | ReasoningEffort;

export interface ModelParamDraft {
  temperature: string;
  max_tokens: string;
  thinking: ThinkingDraft;
  reasoning_effort: ReasoningDraft;
}

export type ModelParamErrors = Partial<Record<keyof ModelParamDraft, string>>;

export interface ModelParamPatchResult {
  /** Only the changed fields; explicit null means "clear this override". */
  patch: ModelParamsUpdate;
  errors: ModelParamErrors;
  changed: boolean;
}

/** Initial form state from the entry's raw values ("" = not set). */
export function draftFromEntry(entry: ModelEntry): ModelParamDraft {
  return {
    temperature: entry.temperature == null ? "" : String(entry.temperature),
    max_tokens: entry.max_tokens == null ? "" : String(entry.max_tokens),
    thinking: entry.thinking == null ? "inherit" : entry.thinking ? "on" : "off",
    reasoning_effort: entry.reasoning_effort ?? "inherit",
  };
}

const TEMPERATURE_LIMIT: Record<string, number> = {
  "openai-compatible": 2,
  anthropic: 1,
};

/**
 * Validate the draft against the provider protocol and build the partial body.
 *
 * Mirrors the backend's numeric write-face rules exactly (temperature range is
 * protocol-specific; max_tokens is a positive integer). The protocol-specific
 * *combination* rules (Anthropic rejects thinking / reasoning_effort) are NOT
 * pre-blocked here on purpose: the service owns that 400 and the page shows its
 * reason verbatim (the brief requires not silently intercepting).
 */
export function modelParamPatch(
  original: ModelEntry,
  draft: ModelParamDraft,
  protocol: string
): ModelParamPatchResult {
  const errors: ModelParamErrors = {};
  const patch: ModelParamsUpdate = {};

  const temperatureText = draft.temperature.trim();
  let temperature: number | null = null;
  if (temperatureText !== "") {
    const parsed = Number(temperatureText);
    const limit = TEMPERATURE_LIMIT[protocol] ?? 2;
    if (!Number.isFinite(parsed)) {
      errors.temperature = "填数字，例如 0.2";
    } else if (parsed < 0 || parsed > limit) {
      errors.temperature = `${protocolLabel(protocol)} 的 temperature 范围是 0–${limit}`;
    } else {
      temperature = parsed;
    }
  }
  if (!errors.temperature && temperature !== (original.temperature ?? null)) {
    patch.temperature = temperature;
  }

  const maxTokensText = draft.max_tokens.trim();
  let maxTokens: number | null = null;
  if (maxTokensText !== "") {
    const parsed = Number(maxTokensText);
    if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed < 1) {
      errors.max_tokens = "填正整数，例如 4096";
    } else {
      maxTokens = parsed;
    }
  }
  if (!errors.max_tokens && maxTokens !== (original.max_tokens ?? null)) {
    patch.max_tokens = maxTokens;
  }

  const thinking: boolean | null =
    draft.thinking === "inherit" ? null : draft.thinking === "on";
  if (thinking !== (original.thinking ?? null)) patch.thinking = thinking;

  const reasoning: ReasoningEffort | null =
    draft.reasoning_effort === "inherit" ? null : draft.reasoning_effort;
  if (reasoning !== (original.reasoning_effort ?? null)) {
    patch.reasoning_effort = reasoning;
  }

  return { patch, errors, changed: Object.keys(patch).length > 0 };
}

/** What "未设置" inherits, shown as the field placeholder. Provider-level
 * defaults are display-only in this slice (editing them stays out of scope). */
export function paramInheritHints(
  provider: Provider,
  protocol: string
): Record<"temperature" | "max_tokens" | "thinking" | "reasoning_effort", string> {
  const temperature =
    provider.temperature == null
      ? "未设置（内置默认 0.2）"
      : `继承 Provider 默认 ${provider.temperature}`;
  const maxTokens =
    provider.max_tokens == null
      ? protocol === PROTOCOL_ANTHROPIC
        ? "未设置（内置默认 4096）"
        : "未设置（内置默认）"
      : `继承 Provider 默认 ${provider.max_tokens}`;
  const thinking =
    provider.thinking == null
      ? "继承 Provider（未设置）"
      : `继承 Provider 默认（${provider.thinking ? "开" : "关"}）`;
  const reasoning =
    provider.reasoning_effort == null
      ? "继承 Provider（未设置）"
      : `继承 Provider 默认（${provider.reasoning_effort}）`;
  return {
    temperature,
    max_tokens: maxTokens,
    thinking,
    reasoning_effort: reasoning,
  };
}
