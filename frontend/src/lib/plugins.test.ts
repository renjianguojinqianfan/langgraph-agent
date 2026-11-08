import { describe, expect, it } from "vitest";
import type { PluginPackageInfo, PluginPresetInfo } from "../types";
import {
  activeFaceSummary,
  buildPluginsPayload,
  changePreset,
  effectivePackageEnabled,
  overrideState,
  packageBadges,
  pluginsDirty,
  pluginsDraftFrom,
  presetLabel,
  presetPackages,
  restartNotice,
  setOverride,
  type PluginsDraft,
} from "./plugins";

// #67 P4-2 / D-24: the tri-state ↔ PUT body mapping and the active-face copy.

const presets: PluginPresetInfo[] = [
  { id: "minimal", packages: ["files", "bash"] },
  { id: "research", packages: ["files", "bash", "skills", "web-search", "subagent", "mcp"] },
  { id: "full", packages: ["files", "bash", "skills", "web-search", "subagent", "mcp"] },
];

const packages: PluginPackageInfo[] = [
  { id: "files", tools: ["read", "write"], connects_mcp: false, affects_subagent_face: false },
  { id: "bash", tools: ["bash"], connects_mcp: false, affects_subagent_face: false },
  { id: "mcp", tools: [], connects_mcp: true, affects_subagent_face: false },
  { id: "subagent", tools: ["spawn_subagent"], connects_mcp: false, affects_subagent_face: true },
];

const draft = (overrides: Partial<PluginsDraft> = {}): PluginsDraft => ({
  preset: "full",
  states: { files: "follow", bash: "follow", mcp: "follow", subagent: "follow" },
  ...overrides,
});

describe("preset labels / package list", () => {
  it("maps the fixed presets and falls back to the raw id", () => {
    expect(presetLabel("minimal")).toBe("最小");
    expect(presetLabel("research")).toBe("研究");
    expect(presetLabel("full")).toBe("完整");
    expect(presetLabel("custom-future")).toBe("custom-future");
  });

  it("reads a preset's packages; unknown preset = empty list", () => {
    expect(presetPackages(presets, "minimal")).toEqual(["files", "bash"]);
    expect(presetPackages(presets, "nope")).toEqual([]);
  });
});

describe("tri-state ↔ saved overrides ↔ PUT body", () => {
  it("derives follow / on / off from saved overrides", () => {
    const overrides = { files: true, bash: false };
    expect(overrideState(overrides, "files")).toBe("on");
    expect(overrideState(overrides, "bash")).toBe("off");
    expect(overrideState(overrides, "mcp")).toBe("follow");
  });

  it("builds the draft from saved + manifest", () => {
    const result = pluginsDraftFrom(
      { preset: "research", overrides: { mcp: false } },
      packages
    );
    expect(result.preset).toBe("research");
    expect(result.states).toEqual({
      files: "follow",
      bash: "follow",
      mcp: "off",
      subagent: "follow",
    });
  });

  it("omits follow from the PUT body and keeps on/off booleans", () => {
    const result = buildPluginsPayload(
      draft({ states: { files: "on", bash: "off", mcp: "follow", subagent: "follow" } })
    );
    expect(result).toEqual({ preset: "full", overrides: { files: true, bash: false } });
  });

  it("switching preset keeps the explicit overrides", () => {
    const current = draft({ states: { files: "off", bash: "follow", mcp: "follow", subagent: "on" } });
    const next = changePreset(current, "minimal");
    expect(next.preset).toBe("minimal");
    expect(next.states.files).toBe("off");
    expect(next.states.subagent).toBe("on");
  });

  it("setOverride replaces one row without touching the others", () => {
    const next = setOverride(draft(), "mcp", "on");
    expect(next.states.mcp).toBe("on");
    expect(next.states.files).toBe("follow");
  });
});

describe("pluginsDirty (save-button guard)", () => {
  const saved = { preset: "full", overrides: {} };

  it("is false when the draft equals the saved config", () => {
    expect(pluginsDirty(saved, draft())).toBe(false);
  });

  it("is true for a preset change or an override change", () => {
    expect(pluginsDirty(saved, changePreset(draft(), "minimal"))).toBe(true);
    expect(pluginsDirty(saved, setOverride(draft(), "mcp", "off"))).toBe(true);
    expect(pluginsDirty({ preset: "full", overrides: { mcp: false } }, setOverride(draft(), "mcp", "on"))).toBe(true);
  });

  it("counts a stale saved override for a package no longer in the manifest", () => {
    expect(pluginsDirty({ preset: "full", overrides: { retired: true } }, draft())).toBe(true);
  });
});

describe("effective package face (backend active is the authority)", () => {
  it("tests membership in the backend-computed active list only", () => {
    expect(effectivePackageEnabled(["files", "bash"], "files")).toBe(true);
    expect(effectivePackageEnabled(["files", "bash"], "mcp")).toBe(false);
    expect(effectivePackageEnabled([], "files")).toBe(false);
  });
});

describe("active face / restart copy", () => {
  it("summarises the assembled packages and tools", () => {
    expect(activeFaceSummary({ packages: ["files", "bash"], tools: ["read", "write", "bash"] })).toEqual({
      packageCount: 2,
      toolCount: 3,
      summary: "2 个能力包 · 3 个工具",
    });
    expect(activeFaceSummary({ packages: [], tools: [] }).summary).toBe("0 个能力包 · 0 个工具");
  });

  it("explains the restart state both ways", () => {
    expect(restartNotice(true)).toContain("重启后台");
    expect(restartNotice(false)).toContain("无需重启");
  });

  it("builds package badges including side effects", () => {
    expect(packageBadges(packages[0])).toEqual(["2 个工具"]);
    expect(packageBadges(packages[2])).toEqual(["0 个工具", "连接 MCP"]);
    expect(packageBadges(packages[3])).toEqual(["1 个工具", "影响子代理面"]);
  });
});
