import type { Task, TaskMode } from "../types";
import { isParked, isStoppable } from "./taskState";

/**
 * #342: the `/` command menu, derived as pure functions (D-23 — no DOM in
 * tests). The component only wires the textarea caret, the task snapshot and
 * these answers; trigger detection, filtering and availability have no React
 * in them.
 */

export type SlashCommandId =
  | "new"
  | "stop"
  | "plan"
  | "build"
  | "model"
  | "resume"
  | "tasks"
  | "help";

export interface SlashCommandContext {
  task: Task | undefined;
  /** The mode a new task would use when no task is selected. */
  mode: TaskMode;
  /** At least one model entry exists in the catalog. */
  hasModels: boolean;
}

export interface SlashCommandItem {
  id: SlashCommandId;
  /** Command name without the leading slash. */
  name: string;
  /** What the menu renders, e.g. `/new`. */
  label: string;
  description: string;
  enabled: boolean;
  /** Why a disabled command cannot run; null when enabled. */
  reason: string | null;
}

export interface SlashToken {
  /** Index of the leading `/`. */
  start: number;
  /** End of the whitespace-delimited token (exclusive). */
  end: number;
  /** Text between `/` and the caret, used as the filter prefix. */
  query: string;
}

export interface SlashMenuState {
  open: boolean;
  token: SlashToken | null;
  items: SlashCommandItem[];
}

const COMMANDS: ReadonlyArray<Omit<SlashCommandItem, "label" | "enabled" | "reason">> = [
  { id: "new", name: "new", description: "新建任务并清空输入" },
  { id: "stop", name: "stop", description: "停止当前运行中的任务" },
  { id: "plan", name: "plan", description: "切换到计划模式（只读工具）" },
  { id: "build", name: "build", description: "切换到执行模式（全部工具）" },
  { id: "model", name: "model", description: "选择下一轮使用的模型" },
  { id: "resume", name: "resume", description: "继续已停下的任务" },
  { id: "tasks", name: "tasks", description: "打开任务列表" },
  { id: "help", name: "help", description: "查看全部命令说明" },
];

const PUNCTUATION = /[\p{P}\p{S}]/u;
/** Whitespace / punctuation that may precede a command, minus path and URL
 * joiners: `:` and `/` belong to URLs, `.` / `~` / `\` to paths. */
const NOT_A_BOUNDARY = new Set([".", ":", "/", "\\", "~"]);

function isBoundary(char: string): boolean {
  return /\s/u.test(char) || (PUNCTUATION.test(char) && !NOT_A_BOUNDARY.has(char));
}

/**
 * The `/` token the caret currently sits in, or null when the caret is not in
 * a command token. A token only counts when its slash is at the start, after
 * whitespace, or after punctuation — so `src/main.ts`, `https://…` and
 * `foo/bar` never open the menu.
 */
export function slashTokenAt(text: string, caret: number): SlashToken | null {
  if (!text || caret < 0) return null;
  const pos = Math.min(caret, text.length);
  if (pos === 0) return null;

  // The whitespace-delimited token containing the caret. A caret after a
  // space therefore reads the *next* token (which has no slash before it).
  let tokenStart = pos;
  while (tokenStart > 0 && !/\s/u.test(text[tokenStart - 1])) tokenStart -= 1;

  const slash = text.lastIndexOf("/", pos - 1);
  if (slash < tokenStart) return null;

  if (slash > 0 && !isBoundary(text[slash - 1])) return null;

  const query = text.slice(slash + 1, pos);
  // `/usr/bin`, `//cdn` and `https://` all leave a slash (or backslash) in the
  // prefix; none of them is a command token.
  if (/[/\\]/u.test(query)) return null;
  // A caret before the second slash of `https:/|/` must not open either.
  if (text[slash + 1] === "/" || text[slash + 1] === "\\") return null;

  let end = pos;
  while (end < text.length && !/\s/u.test(text[end])) end += 1;

  return { start: slash, end, query };
}

function availability(
  command: SlashCommandId,
  context: SlashCommandContext
): { enabled: boolean; reason: string | null } {
  const { task, hasModels } = context;
  const mode = task?.mode ?? context.mode;
  switch (command) {
    case "new":
    case "tasks":
    case "help":
      return { enabled: true, reason: null };
    case "stop":
      return task && isStoppable(task.status)
        ? { enabled: true, reason: null }
        : { enabled: false, reason: "没有运行中的任务" };
    case "resume":
      if (!task) return { enabled: false, reason: "没有可继续的任务" };
      if (isStoppable(task.status)) {
        return { enabled: false, reason: "任务仍在运行" };
      }
      return isParked(task.status)
        ? { enabled: true, reason: null }
        : { enabled: false, reason: "没有可继续的任务" };
    case "plan":
      return mode === "plan"
        ? { enabled: false, reason: "当前任务已在计划模式" }
        : { enabled: true, reason: null };
    case "build":
      return mode === "build"
        ? { enabled: false, reason: "当前任务已在执行模式" }
        : { enabled: true, reason: null };
    case "model":
      return hasModels
        ? { enabled: true, reason: null }
        : { enabled: false, reason: "没有可选模型" };
  }
}

/** Built-in commands filtered by their name prefix (the text after `/`). */
export function slashCommandItems(
  query: string,
  context: SlashCommandContext
): SlashCommandItem[] {
  const prefix = query.toLowerCase();
  return COMMANDS.filter((command) =>
    command.name.toLowerCase().startsWith(prefix)
  ).map((command) => ({
    ...command,
    label: `/${command.name}`,
    ...availability(command.id, context),
  }));
}

/** One pure answer: is the menu open, which token, which options. */
export function slashMenuState(
  text: string,
  caret: number,
  context: SlashCommandContext
): SlashMenuState {
  const token = slashTokenAt(text, caret);
  if (!token) return { open: false, token: null, items: [] };
  return {
    open: true,
    token,
    items: slashCommandItems(token.query, context),
  };
}

/** Highlight the first runnable option; with every option disabled keep the
 * first one highlighted so its reason is visible. */
export function firstEnabledIndex(items: ReadonlyArray<{ enabled: boolean }>): number {
  if (items.length === 0) return -1;
  const index = items.findIndex((item) => item.enabled);
  return index >= 0 ? index : 0;
}

/** ↑/↓ with wrap-around; an empty list has no highlight. */
export function moveSelection(index: number, delta: number, length: number): number {
  if (length <= 0) return -1;
  if (index < 0) return delta > 0 ? 0 : length - 1;
  return (index + delta + length) % length;
}

/**
 * Remove the executed `/token` while keeping the rest of the draft. One
 * joining space (or a space before trailing punctuation) is folded so
 * `hello /new` → `hello` and `hello /new world` → `hello world`.
 */
export function removeCommandToken(text: string, token: SlashToken): string {
  let before = text.slice(0, token.start);
  let removeEnd = token.end;
  // Punctuation typed after the command ("/help!") belongs to the draft, not
  // to the token being removed.
  while (
    removeEnd > token.start + 1 &&
    PUNCTUATION.test(text[removeEnd - 1])
  ) {
    removeEnd -= 1;
  }
  let after = text.slice(removeEnd);

  if (/\s$/u.test(before)) {
    if (/^\s/u.test(after)) after = after.replace(/^\s+/u, "");
    else if (PUNCTUATION.test(after[0] ?? "")) before = before.replace(/\s+$/u, "");
  }
  if (!before.trim()) after = after.replace(/^\s+/u, "");
  if (!after.trim()) return before.replace(/\s+$/u, "");

  return before + after;
}
