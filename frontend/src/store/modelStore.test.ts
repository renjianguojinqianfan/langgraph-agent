import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ApiResponse,
  ModelEntry,
  Provider,
  ProvidersData,
} from "../types";

vi.mock("../api/client", () => ({
  apiErrorText: (r: { detail?: string } | null) =>
    r && typeof r.detail === "string" ? r.detail : "操作失败",
  listProviderModels: vi.fn(),
  listProviders: vi.fn(),
}));

import { listProviderModels, listProviders } from "../api/client";
import { useModelStore } from "./modelStore";

const listProvidersMock = vi.mocked(listProviders);
const listProviderModelsMock = vi.mocked(listProviderModels);

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
  temperature: null,
  max_tokens: null,
  thinking: null,
  reasoning_effort: null,
});

const entry = (providerId: string, model: string): ModelEntry => ({
  id: `${providerId}-${model}`,
  provider_id: providerId,
  model,
  name: `Model ${model}`,
  temperature: null,
  max_tokens: null,
  thinking: null,
  reasoning_effort: null,
});

function providersResponse(ids: string[]): ApiResponse<ProvidersData> {
  return {
    code: 0,
    message: "ok",
    data: {
      providers: ids.map(provider),
      default: ids.length ? `${ids[0]}-m1` : "",
    },
  };
}

function catalogResponse(
  providerId: string,
  models: string[]
): ApiResponse<{ models: ModelEntry[]; default: string }> {
  return {
    code: 0,
    message: "ok",
    data: {
      models: models.map((model) => entry(providerId, model)),
      default: `${providerId}-m1`,
    },
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

describe("modelStore refresh (#243 P2-3 / #267 P2-4b)", () => {
  it("populates provider cards + raw per-provider catalogs + default", async () => {
    listProvidersMock.mockResolvedValue(providersResponse(["deepseek", "bailian"]));
    listProviderModelsMock.mockImplementation((id) =>
      Promise.resolve(catalogResponse(id, [id === "deepseek" ? "m1" : "qwen-max"]))
    );

    await useModelStore.getState().refresh();

    const state = useModelStore.getState();
    expect(state.providers.map((p) => p.id)).toEqual(["deepseek", "bailian"]);
    expect(state.models.map((m) => m.id)).toEqual([
      "deepseek-m1",
      "bailian-qwen-max",
    ]);
    // Raw override fields travel through the catalog read (D-36 edit source).
    expect(state.models[0].temperature).toBeNull();
    expect(state.defaultId).toBe("deepseek-m1");
    expect(state.error).toBe("");
    expect(state.loading).toBe(false);
  });

  it("drops a stale refresh: an earlier read resolving last cannot overwrite the newer answer", async () => {
    const firstProviders = deferred<ApiResponse<ProvidersData>>();
    const secondProviders = deferred<ApiResponse<ProvidersData>>();
    const oldCatalog = deferred<ApiResponse<{ models: ModelEntry[]; default: string }>>();
    listProvidersMock
      .mockReturnValueOnce(firstProviders.promise)
      .mockReturnValueOnce(secondProviders.promise);
    listProviderModelsMock.mockImplementation((id) =>
      id === "new" ? Promise.resolve(catalogResponse("new", ["m1"])) : oldCatalog.promise
    );

    const first = useModelStore.getState().refresh();
    const second = useModelStore.getState().refresh();

    secondProviders.resolve(providersResponse(["new"]));
    await second;
    expect(useModelStore.getState().providers.map((p) => p.id)).toEqual(["new"]);

    firstProviders.resolve(providersResponse(["old"]));
    oldCatalog.resolve(catalogResponse("old", ["m1"]));
    await first;

    const state = useModelStore.getState();
    expect(state.providers.map((p) => p.id)).toEqual(["new"]);
    expect(state.models.map((m) => m.provider_id)).toEqual(["new"]);
    expect(state.defaultId).toBe("new-m1");
    expect(state.loading).toBe(false);
  });

  it("names a failed provider read and keeps the last snapshot", async () => {
    listProvidersMock.mockResolvedValue(providersResponse(["deepseek"]));
    listProviderModelsMock.mockResolvedValue(catalogResponse("deepseek", ["m1"]));
    await useModelStore.getState().refresh();

    listProvidersMock.mockResolvedValue({
      code: 1,
      detail: "providers blew up",
    } as unknown as ApiResponse<ProvidersData>);
    await useModelStore.getState().refresh();

    const state = useModelStore.getState();
    expect(state.providers.map((p) => p.id)).toEqual(["deepseek"]);
    expect(state.error).toContain("providers blew up");
    expect(state.loading).toBe(false);
  });

  it("names a failed catalog read and keeps the last snapshot", async () => {
    listProvidersMock.mockResolvedValue(providersResponse(["deepseek"]));
    listProviderModelsMock.mockResolvedValue(catalogResponse("deepseek", ["m1"]));
    await useModelStore.getState().refresh();

    listProviderModelsMock.mockResolvedValue({
      code: 1,
      detail: "catalog blew up",
    } as unknown as ApiResponse<{ models: ModelEntry[]; default: string }>);
    await useModelStore.getState().refresh();

    const state = useModelStore.getState();
    expect(state.models.map((m) => m.provider_id)).toEqual(["deepseek"]);
    expect(state.error).toContain("catalog blew up");
    expect(state.error).not.toContain("providers blew up");
    expect(state.loading).toBe(false);
  });
});
