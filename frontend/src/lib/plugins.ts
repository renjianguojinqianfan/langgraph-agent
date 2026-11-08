import type {
  PluginActive,
  PluginConfigured,
  PluginPackageInfo,
  PluginPresetInfo,
  PluginsSaveRequest,
} from "../types";

/**
 * #67 P4-2: pure derivations behind the settings page "能力" section.
 *
 * The saved configuration is a preset plus explicit per-package overrides
 * (true = force on, false = force off, absent = follow the preset). The UI edits
 * a tri-state per package; "follow" is expressed by *omitting* the id from the
 * PUT body, so these helpers own that mapping (D-23: no jsdom, assert here).
 */

export type OverrideState = "follow" | "on" | "off";

/** Display names for the fixed presets; unknown ids fall back to the raw id. */
export const PRESET_LABELS: Record<string, string> = {
  minimal: "最小",
  research: "研究",
  full: "完整",
};

export function presetLabel(id: string): string {
  return PRESET_LABELS[id] ?? id;
}

export function presetPackages(
  presets: readonly PluginPresetInfo[],
  presetId: string
): string[] {
  return presets.find((preset) => preset.id === presetId)?.packages ?? [];
}

/** Saved overrides → the row's tri-state. */
export function overrideState(
  overrides: Record<string, boolean>,
  packageId: string
): OverrideState {
  const value = overrides[packageId];
  if (value === true) return "on";
  if (value === false) return "off";
  return "follow";
}

export interface PluginsDraft {
  preset: string;
  states: Record<string, OverrideState>;
}

/** Form state from the saved configuration + the package manifest. */
export function pluginsDraftFrom(
  saved: PluginConfigured,
  packages: readonly PluginPackageInfo[]
): PluginsDraft {
  const states: Record<string, OverrideState> = {};
  for (const pkg of packages) states[pkg.id] = overrideState(saved.overrides, pkg.id);
  return { preset: saved.preset, states };
}

/** Draft → PUT body. "follow" is omitted; on/off travel as booleans. */
export function buildPluginsPayload(draft: PluginsDraft): PluginsSaveRequest {
  const overrides: Record<string, boolean> = {};
  for (const [packageId, state] of Object.entries(draft.states)) {
    if (state === "on") overrides[packageId] = true;
    else if (state === "off") overrides[packageId] = false;
  }
  return { preset: draft.preset, overrides };
}

/** Switching preset keeps explicit overrides: they are intentional exceptions
 * layered on top of whatever preset is selected. */
export function changePreset(draft: PluginsDraft, preset: string): PluginsDraft {
  return { ...draft, preset };
}

export function setOverride(
  draft: PluginsDraft,
  packageId: string,
  state: OverrideState
): PluginsDraft {
  return { ...draft, states: { ...draft.states, [packageId]: state } };
}

/** Whether the draft differs from what the file holds (save-button guard).
 * A stale saved override for a package the manifest no longer lists also counts
 * as a difference: saving would clean it up. */
export function pluginsDirty(
  saved: PluginConfigured,
  draft: PluginsDraft
): boolean {
  if (draft.preset !== saved.preset) return true;
  const draftOverrides = buildPluginsPayload(draft).overrides;
  for (const packageId of Object.keys(saved.overrides)) {
    if (!(packageId in draft.states)) return true;
  }
  const savedIds = Object.keys(saved.overrides);
  const draftIds = Object.keys(draftOverrides);
  if (savedIds.length !== draftIds.length) return true;
  return draftIds.some(
    (packageId) => saved.overrides[packageId] !== draftOverrides[packageId]
  );
}

/** Whether a package is in the backend's active face. The backend already
 * computed preset ∩ overrides; the page must NOT re-derive that rule — it only
 * tests membership in `active.packages` (source of truth). */
export function effectivePackageEnabled(
  activePackages: readonly string[],
  packageId: string
): boolean {
  return activePackages.includes(packageId);
}

export interface ActiveFaceSummary {
  packageCount: number;
  toolCount: number;
  /** One machine-fact line for the active-face header. */
  summary: string;
}

/** Compact summary of what the running backend assembled (GET active). */
export function activeFaceSummary(
  active: Pick<PluginActive, "packages" | "tools">
): ActiveFaceSummary {
  const packageCount = active.packages.length;
  const toolCount = active.tools.length;
  return {
    packageCount,
    toolCount,
    summary: `${packageCount} 个能力包 · ${toolCount} 个工具`,
  };
}

/** Restart hint next to the save button / active face. */
export function restartNotice(restartRequired: boolean): string {
  return restartRequired
    ? "已保存的配置与当前生效面不同；重启后台后生效。"
    : "当前生效面与已保存配置一致，无需重启。";
}

/** Badges for one package row: tool count + side effects. */
export function packageBadges(pkg: PluginPackageInfo): string[] {
  const badges = [`${pkg.tools.length} 个工具`];
  if (pkg.connects_mcp) badges.push("连接 MCP");
  if (pkg.affects_subagent_face) badges.push("影响子代理面");
  return badges;
}
