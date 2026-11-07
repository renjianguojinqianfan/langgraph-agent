import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ApiResponse,
  ModelProfile,
  ModelsData,
  Provider,
  ProvidersData,
} from "../types";

vi.mock("../api/client", () => ({
  apiErrorText: (r: { detail?: string } | null) =>
    r && typeof r.detail === "string" ? r.detail : "操作失败",
  listModels: vi.fn(),
  listProviders: vi.fn(),
}));

import { listModels, listProviders } from "../api/client";
import { useModelStore } from "./modelStore";

const listProvidersMock = vi.mocked(listProviders);
const listModelsMock = vi.mocked(listModels);

/** A promise plus its resolver, so two refreshes can finish out of order. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const provider = (id: string): Provider => ({
  id,
  name: `Provider ${id}`,
  protocol: "openai-compatible",
  base_url: `https://${id}.example.com/v1`,
  api_key_env: "LLM_API_KEY",
  preset: false,
  key_set: false,
  key_source: "none",
});

const profile = (id: string): ModelProfile => ({
  id: `${id}-model`,
  name: `Model ${id}`,
  base_url: `https://${id}.example.com/v1`,
  model: `${id}-chat`,
  api_key_env: "LLM_API_KEY",
  preset: false,
  key_set: false,
  key_source: "none",
  provider_id: id,
  protocol: "openai-compatible",
});

function providersResponse(id: string): ApiResponse<ProvidersData> {
  return {
    code: 0,
    message: "ok",
    data: { providers: [provider(id)], default: `${id}-model` },
  };
}

function modelsResponse(id: string): ApiResponse<ModelsData> {
  return {
    code: 0,
    message: "ok",
    data: { default: `${id}-model`, models: [profile(id)] },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useModelStore.setState({
    providers: [],
    models: [],
    defaultId: "",
    loading: false,
    error: "",
  });
});

describe("modelStore refresh (#243 P2-3)", () => {
  it("populates provider cards + entry ids + default from the two compatibility reads", async () => {
    listProvidersMock.mockResolvedValue(providersResponse("deepseek"));
    listModelsMock.mockResolvedValue(modelsResponse("deepseek"));

    await useModelStore.getState().refresh();

    const state = useModelStore.getState();
    expect(state.providers.map((p) => p.id)).toEqual(["deepseek"]);
    expect(state.models).toEqual([
      {
        id: "deepseek-model",
        provider_id: "deepseek",
        model: "deepseek-chat",
        name: "Model deepseek",
      },
    ]);
    expect(state.defaultId).toBe("deepseek-model");
    expect(state.error).toBe("");
    expect(state.loading).toBe(false);
  });

  it("drops a stale refresh: an earlier read resolving last cannot overwrite the newer answer (#299 LOW-9)", async () => {
    const firstProviders = deferred<ApiResponse<ProvidersData>>();
    const firstModels = deferred<ApiResponse<ModelsData>>();
    const secondProviders = deferred<ApiResponse<ProvidersData>>();
    const secondModels = deferred<ApiResponse<ModelsData>>();
    listProvidersMock
      .mockReturnValueOnce(firstProviders.promise)
      .mockReturnValueOnce(secondProviders.promise);
    listModelsMock
      .mockReturnValueOnce(firstModels.promise)
      .mockReturnValueOnce(secondModels.promise);

    const first = useModelStore.getState().refresh();
    const second = useModelStore.getState().refresh();

    secondProviders.resolve(providersResponse("new"));
    secondModels.resolve(modelsResponse("new"));
    await second;
    expect(useModelStore.getState().providers.map((p) => p.id)).toEqual(["new"]);

    firstProviders.resolve(providersResponse("old"));
    firstModels.resolve(modelsResponse("old"));
    await first;

    const state = useModelStore.getState();
    expect(state.providers.map((p) => p.id)).toEqual(["new"]);
    expect(state.models.map((m) => m.provider_id)).toEqual(["new"]);
    expect(state.defaultId).toBe("new-model");
    // The stale finally must not clear the newer refresh's settled state.
    expect(state.loading).toBe(false);
  });

  it("names a failed provider read and keeps the last snapshot", async () => {
    listProvidersMock.mockResolvedValue(providersResponse("deepseek"));
    listModelsMock.mockResolvedValue(modelsResponse("deepseek"));
    await useModelStore.getState().refresh();

    listProvidersMock.mockResolvedValue({
      code: 1,
      detail: "providers blew up",
    } as unknown as ApiResponse<ProvidersData>);
    listModelsMock.mockResolvedValue(modelsResponse("deepseek"));
    await useModelStore.getState().refresh();

    const state = useModelStore.getState();
    // Old snapshot survives; the failed *provider* read is named.
    expect(state.providers.map((p) => p.id)).toEqual(["deepseek"]);
    expect(state.error).toContain("加载模型配置失败");
    expect(state.error).toContain("providers blew up");
    expect(state.error).not.toContain("models blew up");
    expect(state.loading).toBe(false);
  });

  it("names a failed flat-catalog read and keeps the last snapshot", async () => {
    listProvidersMock.mockResolvedValue(providersResponse("deepseek"));
    listModelsMock.mockResolvedValue(modelsResponse("deepseek"));
    await useModelStore.getState().refresh();

    listProvidersMock.mockResolvedValue(providersResponse("deepseek"));
    listModelsMock.mockResolvedValue({
      code: 1,
      detail: "models blew up",
    } as unknown as ApiResponse<ModelsData>);
    await useModelStore.getState().refresh();

    const state = useModelStore.getState();
    expect(state.models.map((m) => m.provider_id)).toEqual(["deepseek"]);
    expect(state.error).toContain("加载模型配置失败");
    expect(state.error).toContain("models blew up");
    expect(state.error).not.toContain("providers blew up");
    expect(state.loading).toBe(false);
  });
});
