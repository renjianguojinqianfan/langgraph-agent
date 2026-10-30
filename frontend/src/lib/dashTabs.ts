/** Right-column tab vocabulary: three fixed panes plus one tab per opened file. */
export type DashTab = "files" | "plan" | "events" | `file:${string}`;

const FILE_PREFIX = "file:";

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
