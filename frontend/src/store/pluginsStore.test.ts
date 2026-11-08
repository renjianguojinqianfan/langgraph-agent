import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResponse, PluginsData } from "../types";

vi.mock("../api/client", () => ({
  apiErrorText: (r: { detail?: string } | null) =>
    r && typeof r.detail === "string" ? r.detail : "操作失败",
  getPlugins: vi.fn(),
  putPlugins: vi.fn(),
}));

import { getPlugins, putPlugins } from "../api/client";
import { usePluginsStore } from "./pluginsStore";

const getPluginsMock = vi.mocked(getPlugins);
const putPluginsMock = vi.mocked(putPlugins);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const pluginsData = (restartRequired = false): PluginsData => ({
  presets: [
    { id: "minimal", packages: ["files", "bash"] },
    { id: "research", packages: ["files", "bash", "mcp"] },
    { id: "full", packages: ["files", "bash", "mcp"] },
  ],
  packages: [
    { id: "files", tools: ["read", "write"], connects_mcp: false, affects_subagent_face: false },
    { id: "bash", tools: ["bash"], connects_mcp: false, affects_subagent_face: false },
    { id: "mcp", tools: [], connects_mcp: true, affects_subagent_face: false },
  ],
  saved: { preset: "full", overrides: {} },
  active: {
    preset: "full",
    overrides: {},
    packages: ["files", "bash", "mcp"],
    tools: ["read", "write", "bash"],
  },
  restart_required: restartRequired,
});

function ok(data: PluginsData): ApiResponse<PluginsData> {
  return { code: 0, message: "ok", data };
}

beforeEach(() => {
  vi.clearAllMocks();
  usePluginsStore.setState({
    data: null,
    loading: false,
    error: "",
    saving: false,
    saveError: "",
    saveNote: "",
  });
});

describe("pluginsStore (#67 P4-2)", () => {
  it("loads the saved + active snapshot", async () => {
    getPluginsMock.mockResolvedValue(ok(pluginsData()));
    await usePluginsStore.getState().refresh();

    const state = usePluginsStore.getState();
    expect(state.data?.active.packages).toEqual(["files", "bash", "mcp"]);
    expect(state.error).toBe("");
    expect(state.loading).toBe(false);
  });

  it("keeps the old snapshot when a refresh fails", async () => {
    getPluginsMock.mockResolvedValue(ok(pluginsData()));
    await usePluginsStore.getState().refresh();

    getPluginsMock.mockResolvedValue({
      code: 1,
      detail: "plugins blew up",
    } as unknown as ApiResponse<PluginsData>);
    await usePluginsStore.getState().refresh();

    const state = usePluginsStore.getState();
    expect(state.data?.saved.preset).toBe("full");
    expect(state.error).toContain("plugins blew up");
    expect(state.loading).toBe(false);
  });

  it("replaces the snapshot with the PUT response and notes a restart", async () => {
    getPluginsMock.mockResolvedValue(ok(pluginsData()));
    await usePluginsStore.getState().refresh();
    putPluginsMock.mockResolvedValue(ok(pluginsData(true)));

    const saved = await usePluginsStore.getState().save({
      preset: "minimal",
      overrides: { mcp: false },
    });

    const state = usePluginsStore.getState();
    expect(saved).toBe(true);
    expect(putPluginsMock).toHaveBeenCalledWith({ preset: "minimal", overrides: { mcp: false } });
    expect(state.data?.restart_required).toBe(true);
    expect(state.saveNote).toContain("已保存");
    expect(state.saveError).toBe("");
    expect(state.saving).toBe(false);
  });

  it("keeps the old data and the server reason when a save fails", async () => {
    getPluginsMock.mockResolvedValue(ok(pluginsData()));
    await usePluginsStore.getState().refresh();
    putPluginsMock.mockResolvedValue({
      code: 1,
      detail: "未知 preset：bogus",
    } as unknown as ApiResponse<PluginsData>);

    const saved = await usePluginsStore.getState().save({ preset: "bogus", overrides: {} });

    const state = usePluginsStore.getState();
    expect(saved).toBe(false);
    expect(state.data?.saved.preset).toBe("full");
    expect(state.saveError).toContain("未知 preset");
    expect(state.saving).toBe(false);
  });

  it("refuses a second save while one is in flight", async () => {
    const pending = deferred<ApiResponse<PluginsData>>();
    putPluginsMock.mockReturnValueOnce(pending.promise);

    const first = usePluginsStore.getState().save({ preset: "minimal", overrides: {} });
    expect(usePluginsStore.getState().saving).toBe(true);
    const second = await usePluginsStore.getState().save({ preset: "full", overrides: {} });
    expect(second).toBe(false);
    expect(putPluginsMock).toHaveBeenCalledTimes(1);

    pending.resolve(ok(pluginsData(true)));
    expect(await first).toBe(true);
    expect(usePluginsStore.getState().saving).toBe(false);
  });
});
