import type { SkillInfo } from "../types";
import { BACKEND_UNREACHABLE } from "./copy";
import type { SlashToken } from "./slashCommands";

/**
 * #345: the skill half of the `/` menu, derived as pure functions (D-23 — no
 * DOM in tests). Discovery keeps same-name home + workspace entries; the menu
 * merges them into one row and the inserted reference carries only the name,
 * because ``load_skill`` loads both layers.
 */

export interface SkillGroupItem {
  name: string;
  description: string;
  /** Unique layers in discovery order (home first). */
  layers: string[];
  /** Machine fact shown on the row: ``home`` / ``workspace`` / both. */
  layerLabel: string;
  /** Uniform with command items so the shared selection helpers apply. */
  enabled: true;
}

export type SkillCatalogPhase = "loading" | "ready" | "error";

/** The whole catalog fetch, as the composer keeps it. */
export interface SkillCatalogSnapshot {
  phase: SkillCatalogPhase;
  enabled: boolean;
  skills: SkillInfo[];
  /** Failure copy for the skill group only (already prefixed). */
  error: string;
}

export type SkillGroupStatus =
  | "hidden"
  | "loading"
  | "disabled"
  | "empty"
  | "no-match"
  | "error"
  | "ready";

export interface SkillGroupView {
  status: SkillGroupStatus;
  items: SkillGroupItem[];
  note: string | null;
  /** The error row offers a retry action; other notes do not. */
  canRetry: boolean;
}

/** While a command filter is typed, status-only rows stay out of the way. */
const HIDDEN_VIEW: SkillGroupView = {
  status: "hidden",
  items: [],
  note: null,
  canRetry: false,
};

/** Merge same-name entries; the first non-empty description wins. */
export function dedupeSkills(skills: ReadonlyArray<SkillInfo>): SkillGroupItem[] {
  const byName = new Map<string, SkillGroupItem>();
  for (const skill of skills) {
    const name = skill.name.trim();
    if (!name) continue;
    const description = (skill.description ?? "").trim();
    const existing = byName.get(name);
    if (!existing) {
      byName.set(name, {
        name,
        description,
        layers: layerList([skill.layer]),
        layerLabel: layerLabel([skill.layer]),
        enabled: true,
      });
      continue;
    }
    if (!existing.description && description) existing.description = description;
    if (skill.layer && !existing.layers.includes(skill.layer)) {
      existing.layers.push(skill.layer);
      existing.layerLabel = layerLabel(existing.layers);
    }
  }
  return [...byName.values()];
}

/** `home + workspace` when both layers carry the name; never a silent pick. */
export function layerLabel(layers: ReadonlyArray<string>): string {
  const unique = layerList(layers);
  const ordered = [
    ...unique.filter((layer) => layer === "home"),
    ...unique.filter((layer) => layer === "workspace"),
    ...unique.filter((layer) => layer !== "home" && layer !== "workspace"),
  ];
  return ordered.join(" + ");
}

function layerList(layers: ReadonlyArray<string>): string[] {
  const seen: string[] = [];
  for (const layer of layers) {
    const value = String(layer ?? "").trim();
    if (value && !seen.includes(value)) seen.push(value);
  }
  return seen;
}

/** Match name or description case-insensitively; empty query keeps all rows. */
export function filterSkills(
  items: ReadonlyArray<SkillGroupItem>,
  query: string
): SkillGroupItem[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...items];
  return items.filter(
    (item) =>
      item.name.toLowerCase().includes(needle) ||
      item.description.toLowerCase().includes(needle)
  );
}

/** The visible reference; only the name, so `load_skill` reads both layers. */
export function skillReference(name: string): string {
  return `使用技能「${name}」：`;
}

/** Replace the `/` token, keep the rest of the draft, caret after the reference. */
export function insertSkillReference(
  text: string,
  token: SlashToken,
  name: string
): { text: string; caret: number } {
  const before = text.slice(0, token.start);
  const after = text.slice(token.end);
  const reference = skillReference(name);
  return { text: before + reference + after, caret: before.length + reference.length };
}

/** Transport / status-preserving failure copy for the skill group only. */
export function skillCatalogFailure(status: number, detail: string): string {
  const reason =
    status === 0 ? BACKEND_UNREACHABLE : detail.trim() || `HTTP ${status}`;
  return `技能目录读取失败：${reason}`;
}

/**
 * What the menu should render for skills in the current filter state.
 *
 * A bare `/` always explains why the group is empty (package off / no skill /
 * loading). While the user types a command filter, status-only notes stay
 * hidden so command rows are not pushed down; a catalog failure always stays
 * visible with its retry row. Empty-name rows never reach the list.
 */
export function skillGroupView(
  catalog: SkillCatalogSnapshot,
  query: string
): SkillGroupView {
  const filtering = query.trim().length > 0;
  if (catalog.phase === "error") {
    return {
      status: "error",
      items: [],
      note: catalog.error || "技能目录读取失败",
      canRetry: true,
    };
  }
  if (catalog.phase === "loading") {
    return filtering
      ? HIDDEN_VIEW
      : {
          status: "loading",
          items: [],
          note: "正在加载技能…",
          canRetry: false,
        };
  }
  if (!catalog.enabled) {
    return filtering
      ? HIDDEN_VIEW
      : {
          status: "disabled",
          items: [],
          note: "技能能力包已关闭；可在 设置 → 能力 打开",
          canRetry: false,
        };
  }

  const all = dedupeSkills(catalog.skills);
  if (all.length === 0) {
    return filtering
      ? HIDDEN_VIEW
      : {
          status: "empty",
          items: [],
          note: "没有可用技能；在 .agents/skills 放置 SKILL.md",
          canRetry: false,
        };
  }

  const items = filterSkills(all, query);
  if (items.length === 0) {
    return {
      status: "no-match",
      items: [],
      note: "没有匹配的技能",
      canRetry: false,
    };
  }
  return { status: "ready", items, note: null, canRetry: false };
}
