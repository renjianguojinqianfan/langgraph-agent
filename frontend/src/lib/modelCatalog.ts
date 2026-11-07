import { BACKEND_UNREACHABLE } from "./copy";
import type {
  DiscoverCandidate,
  ModelEntry,
  ModelKeySource,
  ModelProfile,
  ModelProtocol,
  Provider,
} from "../types";

/**
 * #243 P2-3 / D-34: pure derivations behind the Provider settings page and the
 * Composer's Provider → model picker. State faces live here so they can be
 * asserted without a DOM (docs/frontend-design.md §10.1, D-23/D-24).
 */

// ── catalog projections ──

/** The legacy `/api/models` projection is one flat row per entry + provider;
 * the v2 catalog only needs the entry half. Providers are read separately
 * (`GET /providers`), so one flat read covers every entry id `Task.model` can
 * hold (D-34 compatibility projection). */
export function entriesFromProfiles(
  profiles: readonly ModelProfile[]
): ModelEntry[] {
  return profiles.map((profile) => ({
    id: profile.id,
    // Pre-v2 rows carried one profile == one provider with the same id.
    provider_id: profile.provider_id || profile.id,
    model: profile.model,
    name: profile.name,
  }));
}

export interface ProviderGroup {
  provider: Provider;
  models: ModelEntry[];
}

/** Provider order is the server's; a provider with no entries is kept (its
 * card shows the empty catalog). Entries whose provider is missing are dropped
 * here — the Composer still shows a task's raw id through its fallback option. */
export function groupByProvider(
  providers: readonly Provider[],
  models: readonly ModelEntry[]
): ProviderGroup[] {
  const grouped = new Map<string, ModelEntry[]>();
  for (const entry of models) {
    const bucket = grouped.get(entry.provider_id);
    if (bucket) bucket.push(entry);
    else grouped.set(entry.provider_id, [entry]);
  }
  return providers.map((provider) => ({
    provider,
    models: grouped.get(provider.id) ?? [],
  }));
}

export function findEntry(
  models: readonly ModelEntry[],
  id: string
): ModelEntry | undefined {
  return models.find((entry) => entry.id === id);
}

export function hasEntry(models: readonly ModelEntry[], id: string): boolean {
  return findEntry(models, id) !== undefined;
}

/** The `{provider, model}` pair → catalog entry, when one already exists
 * (the server's `ensure_model_entry` reuses it instead of growing the catalog). */
export function findEntryByModel(
  models: readonly ModelEntry[],
  providerId: string,
  model: string
): ModelEntry | undefined {
  const cleaned = model.trim();
  return models.find(
    (entry) => entry.provider_id === providerId && entry.model === cleaned
  );
}

export function entryProviderId(
  models: readonly ModelEntry[],
  id: string
): string {
  return findEntry(models, id)?.provider_id ?? "";
}

/** Display name for a model entry id. Falls back to the raw id so a deleted
 * entry or an old `Task.model` still renders (D-34 compat); empty = "默认". */
export function modelLabel(models: readonly ModelEntry[], id: string): string {
  if (!id) return "默认";
  return findEntry(models, id)?.name || id;
}

/** What the Composer renders as selected: an existing task's own entry id wins
 * over the draft (D-32), and an empty id means the global default. */
export function selectedModelId(
  hasTask: boolean,
  taskModel: string,
  draftModel: string,
  defaultId: string
): string {
  return (hasTask ? taskModel : draftModel) || defaultId;
}

// ── protocols / key state ──

export const PROTOCOL_OPENAI: ModelProtocol = "openai-compatible";
export const PROTOCOL_ANTHROPIC: ModelProtocol = "anthropic";

/** D-34/D-35: both protocols render; the service refuses an explicit
 * `anthropic` save until the adapter lands (PR B), which is exactly the
 * readable 400 the page shows. */
export const PROTOCOL_OPTIONS: ReadonlyArray<{
  value: ModelProtocol;
  label: string;
  hint: string;
}> = [
  {
    value: PROTOCOL_OPENAI,
    label: "OpenAI Chat Completions",
    hint: "OpenAI 兼容接口（/chat/completions）",
  },
  {
    value: PROTOCOL_ANTHROPIC,
    label: "Anthropic 原生",
    hint: "Anthropic 协议；适配器在后续片落地，当前保存会被服务端拒绝（400）",
  },
];

export function isModelProtocol(value: string): value is ModelProtocol {
  return value === PROTOCOL_OPENAI || value === PROTOCOL_ANTHROPIC;
}

export function protocolLabel(protocol: string): string {
  return protocolLabelMap[protocol] ?? protocol;
}

const protocolLabelMap: Record<string, string> = {
  [PROTOCOL_OPENAI]: "OpenAI Chat Completions",
  [PROTOCOL_ANTHROPIC]: "Anthropic 原生",
};

export const KEY_SOURCE_LABEL: Record<ModelKeySource, string> = {
  pasted: "粘贴",
  env: "环境变量",
  none: "无",
};

/** Card fact: "已设（粘贴 / 环境变量）" or "未设"; the key itself never ships. */
export function keyStateText(
  provider: Pick<Provider, "key_set" | "key_source">
): string {
  return provider.key_set
    ? `已设（${KEY_SOURCE_LABEL[provider.key_source] ?? provider.key_source}）`
    : "未设";
}

// ── provider card actions ──

export interface ProviderDefaultAction {
  /** Entry id "设为默认" would apply; null when the button is disabled. */
  entryId: string | null;
  /** A model entry of this provider already is the global default. */
  isDefault: boolean;
  /** Why the action is unavailable (shown via `title`), "" when enabled. */
  disabledReason: string;
}

/** The global default is a *model entry* id (D-33/D-34), so "设为默认" on a
 * provider card defaults its first entry; per-entry defaults are not exposed
 * by the API. */
export function providerDefaultAction(
  models: readonly ModelEntry[],
  defaultId: string
): ProviderDefaultAction {
  if (models.length === 0) {
    return { entryId: null, isDefault: false, disabledReason: "先给这个 Provider 添加模型" };
  }
  if (models.some((entry) => entry.id === defaultId)) {
    return { entryId: null, isDefault: true, disabledReason: "已是默认模型" };
  }
  return { entryId: models[0].id, isDefault: false, disabledReason: "" };
}

// ── Composer picker ──

export interface ModelSelectGroup {
  providerId: string;
  providerName: string;
  models: ModelEntry[];
}

/** `<optgroup>` source: providers in server order, empty providers skipped,
 * provider id used as the label fallback. */
export function selectGroups(
  providers: readonly Provider[],
  models: readonly ModelEntry[]
): ModelSelectGroup[] {
  return groupByProvider(providers, models)
    .filter((group) => group.models.length > 0)
    .map((group) => ({
      providerId: group.provider.id,
      providerName: group.provider.name || group.provider.id,
      models: group.models,
    }));
}

// ── form validation (D-34 rules mirrored client-side for readable copy) ──

export interface ProviderFormValues {
  id: string;
  name: string;
  protocol: ModelProtocol;
  base_url: string;
  api_key_env: string;
}

export type ProviderFormErrors = Partial<
  Record<"id" | "base_url" | "api_key_env", string>
>;

export const PROVIDER_ID_PATTERN = /^[a-z][a-z0-9_-]*$/;
const ENV_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const HTTP_URL_PATTERN = /^https?:\/\/\S+$/i;

export interface ProviderFormOptions {
  /** Edit cannot change the id (D-34), so only create validates/uniqueness-checks it. */
  editing: boolean;
  /** Ids already taken (create only). */
  existingIds: readonly string[];
}

export function validateProviderForm(
  values: ProviderFormValues,
  options: ProviderFormOptions
): ProviderFormErrors {
  const errors: ProviderFormErrors = {};
  if (!options.editing) {
    const id = values.id.trim();
    if (!id) errors.id = "Provider ID 必填";
    else if (!PROVIDER_ID_PATTERN.test(id)) {
      errors.id = "ID 需以小写字母开头，只含小写字母、数字、- 和 _";
    } else if (options.existingIds.includes(id)) {
      errors.id = "这个 ID 已被占用，换一个";
    }
  }
  const baseUrl = values.base_url.trim();
  if (!baseUrl) errors.base_url = "API 地址必填";
  else if (!HTTP_URL_PATTERN.test(baseUrl)) {
    errors.base_url = "地址需以 http:// 或 https:// 开头";
  }
  const env = values.api_key_env.trim();
  if (!env) errors.api_key_env = "环境变量名必填（没有可填 LLM_API_KEY）";
  else if (!ENV_NAME_PATTERN.test(env)) {
    errors.api_key_env = "需是合法的环境变量名，例如 LLM_API_KEY";
  }
  return errors;
}

export function firstFormError(errors: ProviderFormErrors): string {
  return errors.id ?? errors.base_url ?? errors.api_key_env ?? "";
}

/** Edit body (D-34): `id` is immutable and `protocol` is only sent when it
 * actually changed — an existing `anthropic` card stays editable for its other
 * fields because the service only refuses an explicit protocol write. */
export function providerUpdateBody(
  original: Pick<Provider, "protocol">,
  values: ProviderFormValues
): {
  name: string;
  base_url: string;
  api_key_env: string;
  protocol?: ModelProtocol;
  /** Present only when the dialog sends a write-only key change. */
  key?: string;
} {
  const body: {
    name: string;
    base_url: string;
    api_key_env: string;
    protocol?: ModelProtocol;
    key?: string;
  } = {
    name: values.name.trim(),
    base_url: values.base_url.trim(),
    api_key_env: values.api_key_env.trim() || "LLM_API_KEY",
  };
  if (values.protocol !== original.protocol) body.protocol = values.protocol;
  return body;
}

// ── manual model entry / catalog-outside model ──

/** Manual add form: non-empty and not already under this provider. */
export function manualModelError(
  model: string,
  existing: readonly ModelEntry[]
): string {
  const cleaned = model.trim();
  if (!cleaned) return "模型 ID 必填";
  if (existing.some((entry) => entry.model === cleaned)) {
    return "该模型已在目录中";
  }
  return "";
}

/** Composer "目录外模型" row: provider + non-empty raw model id are required;
 * the service auto-creates the manual entry on the `{provider, model}` switch. */
export function customModelError(providerId: string, model: string): string {
  if (!providerId) return "先选择一个 Provider";
  if (!model.trim()) return "填入目录外的模型 ID";
  return "";
}

// ── discovery preview ──

/** Candidates not yet in the catalog start checked (D-34 preview is read-only
 * until "加入目录" writes each entry). */
export function initialDiscoverySelection(
  candidates: readonly DiscoverCandidate[]
): string[] {
  return candidates.filter((candidate) => !candidate.added).map((candidate) => candidate.model);
}

export function toggleDiscoveryPick(
  picked: readonly string[],
  model: string
): string[] {
  return picked.includes(model)
    ? picked.filter((candidate) => candidate !== model)
    : [...picked, model];
}

export function pickableCandidates(
  candidates: readonly DiscoverCandidate[],
  picked: readonly string[]
): DiscoverCandidate[] {
  const chosen = new Set(picked);
  return candidates.filter(
    (candidate) => !candidate.added && chosen.has(candidate.model)
  );
}

/** Anthropic has no discovery adapter in this slice: explain the reason and
 * point at the manual-add next step instead of firing a doomed request. */
export function discoveryBlockedReason(protocol: string): string {
  return protocol === PROTOCOL_ANTHROPIC
    ? "Anthropic 协议暂不支持自动获取模型目录；请手动添加模型 ID。"
    : "";
}

// ── failure copy (reason + next step, D-31 style) ──

/** Non-OK discover answer: the service's text already names key / address /
 * redirect / timeout causes; append the always-available manual next step. */
export function discoveryFailureNote(detail: string): string {
  const reason = detail.trim() || "无法连接后台；确认服务在运行后再试。";
  return `${reason}也可以手动添加模型 ID。`;
}

/** Non-OK model switch: keep the server reason and name the next check. */
export function modelSwitchFailure(detail: string): string {
  const reason = detail.trim() || "操作失败";
  return `切换失败：${reason}；确认 Provider 地址、Key 与协议后重试。`;
}

/** A failed delete is not one failure (#299 LOW): a 409 means "not this one"
 * (last model), 401 means re-login, 404 means the card is stale, and 0 means
 * the backend is unreachable. Each branch names its own next step instead of
 * appending "add a model" to every error. */
export function providerDeleteFailure(status: number, detail: string): string {
  const reason = detail.trim();
  if (status === 0) return `删除 Provider 失败：${BACKEND_UNREACHABLE}`;
  if (status === 401) {
    return `删除 Provider 失败：${reason || "登录已失效"}；重新登录后再试。`;
  }
  if (status === 404) return "Provider 不存在；刷新设置页后再试。";
  if (status === 409) {
    return `${reason || "删除被拒绝"}；该 Provider 下是最后一个模型，或凭据文件不可写；按原因处理后重试。`;
  }
  return `删除 Provider 失败：${reason || "服务返回错误，稍后重试"}`;
}

/** Model-entry delete: 409 is the catalog's "cannot delete the last model"
 * invariant; 404 means the list is stale. */
export function modelDeleteFailure(status: number, detail: string): string {
  const reason = detail.trim();
  if (status === 0) return `删除模型失败：${BACKEND_UNREACHABLE}`;
  if (status === 401) {
    return `删除模型失败：${reason || "登录已失效"}；重新登录后再试。`;
  }
  if (status === 404) return `${reason || "模型不存在"}；刷新设置页后再试。`;
  if (status === 409) {
    return `${reason || "删除被拒绝"}；目录至少要保留一个模型，先给别的 Provider 添加模型。`;
  }
  return `删除模型失败：${reason || "服务返回错误，稍后重试"}`;
}
