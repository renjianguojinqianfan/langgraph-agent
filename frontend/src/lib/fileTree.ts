import type { WorkspaceFile } from "../types";

export interface FileTreeDir {
  kind: "dir";
  name: string;
  path: string;
  children: FileTreeEntry[];
}

export interface FileTreeFile {
  kind: "file";
  name: string;
  path: string;
  size: number;
}

export type FileTreeEntry = FileTreeDir | FileTreeFile;

/** One rendered explorer row; the tree is flattened, `depth` carries nesting. */
export interface FileTreeRow {
  kind: "dir" | "file";
  name: string;
  path: string;
  size: number;
  /** 0 = directly under the workspace root; `aria-level` is `depth + 1`. */
  depth: number;
  /** Dirs only: whether their children are currently visible. */
  expanded: boolean;
}

const collator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: "base",
});

function compareEntries(a: FileTreeEntry, b: FileTreeEntry): number {
  if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1;
  return collator.compare(a.name, b.name);
}

function sortTree(dir: FileTreeDir): void {
  dir.children.sort(compareEntries);
  for (const child of dir.children) {
    if (child.kind === "dir") sortTree(child);
  }
}

/**
 * #341: the server returns a flat, path-sorted file list. Build the explorer
 * shape from it — directories first, natural name order, empty dirs cannot
 * exist here because the API only lists files.
 */
export function buildFileTree(files: readonly WorkspaceFile[]): FileTreeDir {
  const root: FileTreeDir = { kind: "dir", name: "", path: "", children: [] };
  const dirs = new Map<string, FileTreeDir>([["", root]]);

  for (const file of files) {
    const segments = file.path.split("/").filter(Boolean);
    if (segments.length === 0) continue;

    let parent = root;
    for (let i = 0; i < segments.length - 1; i += 1) {
      const path = segments.slice(0, i + 1).join("/");
      let dir = dirs.get(path);
      if (!dir) {
        dir = { kind: "dir", name: segments[i], path, children: [] };
        dirs.set(path, dir);
        parent.children.push(dir);
      }
      parent = dir;
    }

    parent.children.push({
      kind: "file",
      name: segments[segments.length - 1],
      path: file.path,
      size: file.size,
    });
  }

  sortTree(root);
  return root;
}

/**
 * Flatten the visible slice of the tree for rendering. The root is always
 * open — it is the tree itself, not a row — and every other directory is open
 * only when its path is in `expanded`.
 */
export function fileTreeRows(
  root: FileTreeDir,
  expanded: ReadonlySet<string>,
  query = ""
): FileTreeRow[] {
  const needle = query.trim().toLowerCase();
  if (needle) return filteredRows(root, needle);

  const rows: FileTreeRow[] = [];

  const walk = (dir: FileTreeDir, depth: number): void => {
    for (const entry of dir.children) {
      if (entry.kind === "file") {
        rows.push({
          kind: "file",
          name: entry.name,
          path: entry.path,
          size: entry.size,
          depth,
          expanded: false,
        });
        continue;
      }
      const open = expanded.has(entry.path);
      rows.push({
        kind: "dir",
        name: entry.name,
        path: entry.path,
        size: 0,
        depth,
        expanded: open,
      });
      if (open) walk(entry, depth + 1);
    }
  };

  walk(root, 0);
  return rows;
}

/**
 * Active filter: only the branches that contain a match survive, and every
 * surviving directory renders open. The caller's expansion set is untouched,
 * so clearing the filter restores exactly what the user had before.
 */
function filteredRows(root: FileTreeDir, needle: string): FileTreeRow[] {
  const matches = (entry: FileTreeEntry): boolean =>
    entry.kind === "file"
      ? entry.path.toLowerCase().includes(needle)
      : entry.children.some(matches);

  const rows: FileTreeRow[] = [];

  const walk = (dir: FileTreeDir, depth: number): void => {
    for (const entry of dir.children) {
      if (!matches(entry)) continue;
      if (entry.kind === "file") {
        rows.push({
          kind: "file",
          name: entry.name,
          path: entry.path,
          size: entry.size,
          depth,
          expanded: false,
        });
        continue;
      }
      rows.push({
        kind: "dir",
        name: entry.name,
        path: entry.path,
        size: 0,
        depth,
        expanded: true,
      });
      walk(entry, depth + 1);
    }
  };

  walk(root, 0);
  return rows;
}

/** Every ancestor directory of a workspace-relative file path, root-first. */
export function ancestorPaths(path: string): string[] {
  const segments = path.split("/").filter(Boolean);
  const ancestors: string[] = [];
  for (let i = 0; i < segments.length - 1; i += 1) {
    ancestors.push(segments.slice(0, i + 1).join("/"));
  }
  return ancestors;
}

/** Expand every ancestor of `path`; returns the same set when already open. */
export function expandAncestors(
  expanded: ReadonlySet<string>,
  path: string
): ReadonlySet<string> {
  const ancestors = ancestorPaths(path);
  if (ancestors.every((dir) => expanded.has(dir))) return expanded;
  const next = new Set(expanded);
  for (const dir of ancestors) next.add(dir);
  return next;
}

export function toggleDir(
  expanded: ReadonlySet<string>,
  path: string
): ReadonlySet<string> {
  const next = new Set(expanded);
  if (next.has(path)) next.delete(path);
  else next.add(path);
  return next;
}

export type TreeKey =
  | "ArrowUp"
  | "ArrowDown"
  | "ArrowLeft"
  | "ArrowRight"
  | "Home"
  | "End";

const TREE_KEYS: ReadonlySet<string> = new Set<TreeKey>([
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "Home",
  "End",
]);

export function isTreeKey(key: string): key is TreeKey {
  return TREE_KEYS.has(key);
}

export interface TreeKeyAction {
  /** Row that should receive focus after the key, or null to stay put. */
  focusPath: string | null;
  /** Directory the key wants to open/close (the caller flips its set). */
  toggle: string | null;
}

const STAY: TreeKeyAction = { focusPath: null, toggle: null };

/**
 * #341: the ARIA tree keyboard contract as one pure transition. The row's own
 * `expanded` flag decides open/close, so an active filter (which renders every
 * surviving dir open) never writes to the user's saved expansion state.
 * Enter/Space stay in the component (open a file / toggle a directory).
 */
export function treeKeyAction(
  rows: readonly FileTreeRow[],
  currentPath: string,
  key: TreeKey
): TreeKeyAction {
  const index = rows.findIndex((row) => row.path === currentPath);
  if (index < 0) return STAY;
  const row = rows[index];

  switch (key) {
    case "ArrowUp":
      return index > 0 ? { focusPath: rows[index - 1].path, toggle: null } : STAY;
    case "ArrowDown":
      return index < rows.length - 1
        ? { focusPath: rows[index + 1].path, toggle: null }
        : STAY;
    case "Home":
      return rows.length > 0 ? { focusPath: rows[0].path, toggle: null } : STAY;
    case "End":
      return rows.length > 0
        ? { focusPath: rows[rows.length - 1].path, toggle: null }
        : STAY;
    case "ArrowRight": {
      if (row.kind !== "dir") return STAY;
      if (!row.expanded) return { focusPath: null, toggle: row.path };
      const firstChild = rows[index + 1];
      return firstChild && firstChild.depth === row.depth + 1
        ? { focusPath: firstChild.path, toggle: null }
        : STAY;
    }
    case "ArrowLeft": {
      if (row.kind === "dir" && row.expanded) {
        return { focusPath: null, toggle: row.path };
      }
      for (let i = index - 1; i >= 0; i -= 1) {
        if (rows[i].depth < row.depth) {
          return { focusPath: rows[i].path, toggle: null };
        }
      }
      return STAY;
    }
  }
}
