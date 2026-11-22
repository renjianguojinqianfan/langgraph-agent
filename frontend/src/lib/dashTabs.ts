/** Right-column tab vocabulary: three fixed panes plus one tab per opened file
 * and one review tab per opened ``(task, round)`` change set (#343). */
export type DashTab =
  | "files"
  | "plan"
  | "events"
  | `file:${string}`
  | `change:${string}`;

const FILE_PREFIX = "file:";
const CHANGE_PREFIX = "change:";

export function fileTabId(path: string): DashTab {
  return `${FILE_PREFIX}${path}`;
}

export function fileTabPath(tab: DashTab): string | null {
  return tab.startsWith(FILE_PREFIX) ? tab.slice(FILE_PREFIX.length) : null;
}

function baseName(path: string): string {
  return path.split("/").pop() || path;
}

/**
 * Tab label: the basename, or the full relative path when another open tab
 * shares that basename (`a/README.md` + `b/README.md` are not two identical
 * "README.md" chips).
 */
export function fileTabLabel(path: string, opened: readonly string[]): string {
  const name = baseName(path);
  return opened.some((other) => other !== path && baseName(other) === name)
    ? path
    : name;
}

/** Open a file tab once; clicking the same file again only activates it. */
export function openFileTab(opened: readonly string[], path: string): string[] {
  return opened.includes(path) ? [...opened] : [...opened, path];
}

export function closeFileTab(opened: readonly string[], path: string): string[] {
  return opened.filter((open) => open !== path);
}

export interface ChangeTabRef {
  taskId: string;
  round: number;
}

/** One review tab per (task, round); the id itself carries the identity. */
export function changeTabId(taskId: string, round: number): DashTab {
  return `${CHANGE_PREFIX}${taskId}:${round}`;
}

export function changeTabRef(tab: string): ChangeTabRef | null {
  if (!tab.startsWith(CHANGE_PREFIX)) return null;
  const body = tab.slice(CHANGE_PREFIX.length);
  const separator = body.lastIndexOf(":");
  if (separator <= 0) return null;
  const round = Number(body.slice(separator + 1));
  if (!Number.isInteger(round) || round < 1) return null;
  return { taskId: body.slice(0, separator), round };
}

/** True for the tabs a user can close: one opened file or one review round. */
export function isClosableTab(tab: DashTab): boolean {
  return fileTabPath(tab) !== null || changeTabRef(tab) !== null;
}

/** Open a review tab once; the same (task, round) only re-activates it. */
export function openChangeTab(opened: readonly number[], round: number): number[] {
  return opened.includes(round) ? [...opened] : [...opened, round];
}

export function closeChangeTab(opened: readonly number[], round: number): number[] {
  return opened.filter((open) => open !== round);
}

/** Focus after closing a review tab: left neighbour, right neighbour, files. */
export function tabAfterClosingChange(
  opened: readonly number[],
  round: number,
  active: DashTab,
  taskId: string
): DashTab {
  const index = opened.indexOf(round);
  if (index < 0 || active !== changeTabId(taskId, round)) return active;
  const remaining = opened.filter((item) => item !== round);
  const next = remaining[index - 1] ?? remaining[index];
  return next === undefined ? "files" : changeTabId(taskId, next);
}

/**
 * Which tab takes focus after closing `closedPath`: the tab to its left, else
 * the one to its right, else the file list. Closing an inactive tab leaves the
 * active tab alone.
 */
export function tabAfterClosing(
  opened: readonly string[],
  closedPath: string,
  active: DashTab
): DashTab {
  const index = opened.indexOf(closedPath);
  if (index < 0 || fileTabPath(active) !== closedPath) return active;
  const remaining = opened.filter((path) => path !== closedPath);
  const next = remaining[index - 1] ?? remaining[index];
  return next === undefined ? "files" : fileTabId(next);
}
