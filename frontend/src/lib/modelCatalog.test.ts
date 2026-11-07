import { describe, expect, it } from "vitest";
import type {
  DiscoverCandidate,
  ModelEntry,
  ModelProfile,
  Provider,
} from "../types";
import {
  customModelError,
  discoveryBlockedReason,
  discoveryFailureNote,
  entriesFromProfiles,
  entryProviderId,
  findEntryByModel,
  firstFormError,
  groupByProvider,
  hasEntry,
  initialDiscoverySelection,
  isModelProtocol,
  keyStateText,
  manualModelError,
  modelDeleteFailure,
  modelLabel,
  modelSwitchFailure,
  providerDeleteFailure,
  pickableCandidates,
  protocolLabel,
  providerDefaultAction,
  providerLabel,
  providerUpdateBody,
  PROTOCOL_ANTHROPIC,
  PROTOCOL_OPENAI,
  selectGroups,
  selectedModelId,
  toggleDiscoveryPick,
  validateProviderForm,
  type ProviderFormValues,
} from "./modelCatalog";

// #243 P2-3 / D-24: the Provider page and the Composer picker derive every
// state face here, so these assertions are the mechanical half of the slice.

const deepseek = (overrides: Partial<Provider> = {}): Provider => ({
  id: "deepseek",
  name: "DeepSeek",
  protocol: PROTOCOL_OPENAI,
  base_url: "https://api.deepseek.com/v1",
  api_key_env: "LLM_API_KEY",
  preset: true,
  key_set: true,
  key_source: "env",
  ...overrides,
});

const bailian = (overrides: Partial<Provider> = {}): Provider => ({
  id: "bailian",
  name: "百炼",
  protocol: PROTOCOL_OPENAI,
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  api_key_env: "DASHSCOPE_API_KEY",
  preset: false,
  key_set: false,
  key_source: "none",
  ...overrides,
});

const entry = (overrides: Partial<ModelEntry> = {}): ModelEntry => ({
  id: "e-deepseek-chat",
  provider_id: "deepseek",
  model: "deepseek-chat",
  name: "DeepSeek Chat",
  ...overrides,
});

const values = (overrides: Partial<ProviderFormValues> = {}): ProviderFormValues => ({
  id: "my-provider",
  name: "我的供应商",
  protocol: PROTOCOL_OPENAI,
  base_url: "https://api.example.com/v1",
  api_key_env: "MY_API_KEY",
  ...overrides,
});

describe("entriesFromProfiles (legacy /api/models compat projection)", () => {
  it("keeps the entry half and its provider id", () => {
    const profile: ModelProfile = {
      id: "e1",
      name: "Chat",
      base_url: "https://api.example.com/v1",
      model: "chat-1",
      api_key_env: "K",
      preset: false,
      key_set: true,
      key_source: "pasted",
      provider_id: "p1",
      protocol: PROTOCOL_OPENAI,
    };
    expect(entriesFromProfiles([profile])).toEqual([
      { id: "e1", provider_id: "p1", model: "chat-1", name: "Chat" },
    ]);
  });

  it("falls back to the entry id when a pre-v2 row has no provider_id", () => {
    const profile = {
      id: "legacy",
      name: "旧配置",
      base_url: "https://x/v1",
      model: "m",
      api_key_env: "K",
      preset: false,
      key_set: false,
      key_source: "none",
      provider_id: "",
      protocol: PROTOCOL_OPENAI,
    } as ModelProfile;
    expect(entriesFromProfiles([profile])[0].provider_id).toBe("legacy");
  });
});

describe("groupByProvider / selectGroups", () => {
  const providers = [deepseek(), bailian()];
  const models = [
    entry(),
    entry({ id: "e-r1", provider_id: "bailian", model: "qwen-max", name: "Qwen Max" }),
    entry({ id: "e-b1", provider_id: "bailian", model: "qwen-plus", name: "Qwen Plus" }),
  ];

  it("keeps server provider order and keeps an empty provider visible", () => {
    const groups = groupByProvider(providers, [entry(), entry({ id: "e2", provider_id: "orphan" })]);
    expect(groups.map((group) => group.provider.id)).toEqual(["deepseek", "bailian"]);
    expect(groups[0].models.map((model) => model.id)).toEqual(["e-deepseek-chat"]);
    // Provider with no catalog entries is kept (its card shows the empty state).
    expect(groups[1].models).toEqual([]);
    // Entry whose provider is missing is dropped from grouping, not invented.
    expect(groups.flatMap((group) => group.models).map((model) => model.id)).toEqual([
      "e-deepseek-chat",
    ]);
  });

  it("optgroups skip empty providers and fall back to the id for a missing name", () => {
    const unnamed = bailian({ id: "p3", name: "" });
    const groups = selectGroups(
      [deepseek(), bailian(), unnamed],
      [entry(), entry({ id: "e-r1", provider_id: "bailian", model: "qwen-max", name: "Qwen Max" })]
    );
    expect(groups.map((group) => group.providerId)).toEqual(["deepseek", "bailian"]);
    expect(groups[1].providerName).toBe("百炼");
  });

  it("returns [] for no providers or no entries", () => {
    expect(selectGroups([], [entry()])).toEqual([]);
    expect(selectGroups([deepseek()], [])).toEqual([]);
  });
});

describe("entry lookups and labels", () => {
  it("finds entries by id / provider+model (trimmed) and exposes hasEntry", () => {
    const models = [entry(), entry({ id: "e2", provider_id: "bailian", model: "qwen-max" })];
    expect(hasEntry(models, "e2")).toBe(true);
    expect(hasEntry(models, "missing")).toBe(false);
    expect(entryProviderId(models, "e2")).toBe("bailian");
    expect(entryProviderId(models, "missing")).toBe("");
    expect(findEntryByModel(models, "bailian", "  qwen-max  ")?.id).toBe("e2");
    expect(findEntryByModel(models, "deepseek", "qwen-max")).toBeUndefined();
  });

  it("modelLabel uses the entry name, raw id as fallback, 默认 for empty", () => {
    const models = [entry(), entry({ id: "e2", model: "raw-model", name: "" })];
    expect(modelLabel(models, "e-deepseek-chat")).toBe("DeepSeek Chat");
    // A deleted entry / pre-v2 Task.model still renders its raw id (compat).
    expect(modelLabel(models, "gone-id")).toBe("gone-id");
    expect(modelLabel(models, "e2")).toBe("e2");
    expect(modelLabel(models, "")).toBe("默认");
  });

  it("selectedModelId: task entry wins, else draft, else default", () => {
    expect(selectedModelId(true, "task-model", "draft", "default")).toBe("task-model");
    expect(selectedModelId(true, "", "draft", "default")).toBe("default");
    expect(selectedModelId(false, "task-model", "draft", "default")).toBe("draft");
    expect(selectedModelId(false, "", "", "default")).toBe("default");
  });
});

describe("providerDefaultAction (global default is a model entry id)", () => {
  it("defaults the first entry of the provider", () => {
    expect(providerDefaultAction([entry(), entry({ id: "e2" })], "elsewhere")).toEqual({
      entryId: "e-deepseek-chat",
      isDefault: false,
      disabledReason: "",
    });
  });

  it("reports an existing default and disables the action", () => {
    expect(providerDefaultAction([entry(), entry({ id: "e2" })], "e2")).toEqual({
      entryId: null,
      isDefault: true,
      disabledReason: "已是默认模型",
    });
  });

  it("disables on an empty catalog with the next step", () => {
    expect(providerDefaultAction([], "e1").disabledReason).toBe("先给这个 Provider 添加模型");
  });
});

describe("providerLabel (card heading / accessible name)", () => {
  it("keeps the display name and appends the id shown on the card", () => {
    expect(providerLabel(deepseek())).toBe("DeepSeek（deepseek）");
    expect(providerLabel(bailian())).toBe("百炼（bailian）");
  });

  it("does not repeat an id used as the name and falls back to the id", () => {
    expect(providerLabel(deepseek({ name: "deepseek" }))).toBe("deepseek");
    expect(providerLabel(deepseek({ name: "   " }))).toBe("deepseek");
  });
});

describe("protocol / key state copy", () => {
  it("labels both protocols and rejects unknown values", () => {
    expect(protocolLabel(PROTOCOL_OPENAI)).toBe("OpenAI Chat Completions");
    expect(protocolLabel(PROTOCOL_ANTHROPIC)).toBe("Anthropic 原生");
    expect(protocolLabel("weird")).toBe("weird");
    expect(isModelProtocol("anthropic")).toBe(true);
    expect(isModelProtocol("grpc")).toBe(false);
  });

  it("keyStateText never names a secret, only set/source", () => {
    expect(keyStateText(deepseek())).toBe("已设（环境变量）");
    expect(keyStateText(deepseek({ key_source: "pasted" }))).toBe("已设（粘贴）");
    expect(keyStateText(bailian())).toBe("未设");
  });
});

describe("validateProviderForm (create / edit rules mirrored from D-34)", () => {
  it("accepts a well-formed create", () => {
    expect(
      validateProviderForm(values(), { editing: false, existingIds: ["deepseek"] })
    ).toEqual({});
  });

  it("requires a provider id and mirrors the backend pattern", () => {
    const opts = { editing: false, existingIds: [] as string[] };
    expect(validateProviderForm(values({ id: "  " }), opts).id).toContain("必填");
    expect(validateProviderForm(values({ id: "MyProvider" }), opts).id).toContain("小写字母");
    expect(validateProviderForm(values({ id: "1abc" }), opts).id).toContain("小写字母");
    expect(validateProviderForm(values({ id: "my provider" }), opts).id).toContain("小写字母");
    expect(validateProviderForm(values({ id: "my-provider" }), opts).id).toBeUndefined();
  });

  it("refuses a duplicate id on create only (edit keeps the id immutable)", () => {
    expect(
      validateProviderForm(values(), { editing: false, existingIds: ["my-provider"] }).id
    ).toContain("已被占用");
    expect(
      validateProviderForm(values(), { editing: true, existingIds: ["my-provider"] }).id
    ).toBeUndefined();
  });

  it("validates the address and the env var name", () => {
    const opts = { editing: true, existingIds: [] as string[] };
    expect(validateProviderForm(values({ base_url: "" }), opts).base_url).toContain("必填");
    expect(validateProviderForm(values({ base_url: "api.example.com" }), opts).base_url).toContain(
      "http"
    );
    expect(validateProviderForm(values({ base_url: "ftp://x" }), opts).base_url).toContain("http");
    expect(validateProviderForm(values({ api_key_env: "" }), opts).api_key_env).toContain("必填");
    expect(validateProviderForm(values({ api_key_env: "9BAD" }), opts).api_key_env).toContain(
      "环境变量名"
    );
    expect(validateProviderForm(values({ api_key_env: "LLM_API_KEY" }), opts).api_key_env).toBeUndefined();
  });

  it("firstFormError surfaces id → address → env in that order", () => {
    expect(firstFormError({})).toBe("");
    expect(firstFormError({ api_key_env: "c", id: "a", base_url: "b" })).toBe("a");
    expect(firstFormError({ base_url: "b", api_key_env: "c" })).toBe("b");
  });
});

describe("providerUpdateBody (id immutable; unchanged protocol omitted)", () => {
  it("edits an existing anthropic card without re-sending its protocol", () => {
    const original = { protocol: PROTOCOL_ANTHROPIC } as const;
    const body = providerUpdateBody(original, values({ protocol: PROTOCOL_ANTHROPIC }));
    expect(body.protocol).toBeUndefined();
    expect(body).toEqual({
      name: "我的供应商",
      base_url: "https://api.example.com/v1",
      api_key_env: "MY_API_KEY",
    });
  });

  it("sends the protocol only when it changed and defaults the env name", () => {
    const body = providerUpdateBody(
      { protocol: PROTOCOL_OPENAI },
      values({ protocol: PROTOCOL_ANTHROPIC, api_key_env: "  " })
    );
    expect(body.protocol).toBe(PROTOCOL_ANTHROPIC);
    expect(body.api_key_env).toBe("LLM_API_KEY");
  });
});

describe("manual model entry / catalog-outside model", () => {
  it("manualModelError names empty and duplicate ids", () => {
    const existing = [entry()];
    expect(manualModelError("  ", existing)).toContain("必填");
    expect(manualModelError("deepseek-chat", existing)).toContain("已在目录");
    expect(manualModelError("qwen-max", existing)).toBe("");
  });

  it("customModelError requires a provider and a non-empty id", () => {
    expect(customModelError("", "qwen-max")).toContain("Provider");
    expect(customModelError("bailian", "  ")).toContain("模型 ID");
    expect(customModelError("bailian", "qwen-max")).toBe("");
  });
});

describe("discovery preview", () => {
  const candidates: DiscoverCandidate[] = [
    { model: "qwen-max", name: "Qwen Max", added: false },
    { model: "qwen-plus", name: "Qwen Plus", added: false },
    { model: "qwen-turbo", name: "Qwen Turbo", added: true },
  ];

  it("starts with not-added candidates checked", () => {
    expect(initialDiscoverySelection(candidates)).toEqual(["qwen-max", "qwen-plus"]);
  });

  it("toggles a pick without mutating the input", () => {
    const picked = ["qwen-max"];
    expect(toggleDiscoveryPick(picked, "qwen-plus")).toEqual(["qwen-max", "qwen-plus"]);
    expect(toggleDiscoveryPick(picked, "qwen-max")).toEqual([]);
    expect(picked).toEqual(["qwen-max"]);
  });

  it("pickableCandidates drops already-added rows", () => {
    expect(
      pickableCandidates(candidates, ["qwen-max", "qwen-turbo"]).map((c) => c.model)
    ).toEqual(["qwen-max"]);
  });

  it("explains the anthropic protocol next step instead of a doomed request", () => {
    expect(discoveryBlockedReason(PROTOCOL_ANTHROPIC)).toContain("手动添加");
    expect(discoveryBlockedReason(PROTOCOL_OPENAI)).toBe("");
  });
});

describe("failure copy keeps a reason and a next step (D-31 style)", () => {
  it("discoveryFailureNote keeps the server text and points at manual add", () => {
    expect(discoveryFailureNote("认证失败（HTTP 401）：检查该 Provider 的 Key 是否有效。")).toContain(
      "Key 是否有效"
    );
    expect(discoveryFailureNote("认证失败（HTTP 401）")).toContain("手动添加");
    expect(discoveryFailureNote("")).toContain("无法连接后台");
    expect(discoveryFailureNote("")).toContain("手动添加");
  });

  it("modelSwitchFailure names the checks after a server refusal", () => {
    const note = modelSwitchFailure('unknown model "nope"');
    expect(note).toContain("unknown model");
    expect(note).toContain("Provider 地址、Key 与协议");
    expect(modelSwitchFailure("")).toContain("操作失败");
  });
});

describe("status-branched delete copy (#299 LOW-3)", () => {
  it("provider delete names the status-specific next step, not one generic one", () => {
    // 409 = last model or unwritable credentials; keep the server reason.
    const conflict = providerDeleteFailure(409, "cannot delete the last model profile");
    expect(conflict).toContain("cannot delete the last model profile");
    expect(conflict).toContain("最后一个模型");
    // 404 = stale card, 401 = re-login, 0 = backend down.
    expect(providerDeleteFailure(404, "provider not found")).toContain("刷新设置页");
    expect(providerDeleteFailure(401, "invalid token")).toContain("重新登录");
    expect(providerDeleteFailure(0, "")).toContain("无法连接后台");
    // A plain 500 keeps the reason but must NOT prescribe "add a model".
    const boom = providerDeleteFailure(500, "boom");
    expect(boom).toContain("boom");
    expect(boom).not.toContain("最后一个模型");
  });

  it("model delete branches the last-model 409 from stale 404 / unreachable 0", () => {
    const last = modelDeleteFailure(409, "cannot delete the last model profile");
    expect(last).toContain("目录至少要保留一个模型");
    expect(modelDeleteFailure(404, "model not found")).toContain("刷新设置页");
    expect(modelDeleteFailure(401, "invalid token")).toContain("重新登录");
    expect(modelDeleteFailure(0, "")).toContain("无法连接后台");
    expect(modelDeleteFailure(500, "boom")).toContain("boom");
  });
});
