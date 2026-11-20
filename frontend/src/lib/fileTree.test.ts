import { describe, expect, it } from "vitest";
import type { WorkspaceFile } from "../types";
import {
  ancestorPaths,
  buildFileTree,
  expandAncestors,
  fileTreeRows,
  isTreeKey,
  toggleDir,
  treeKeyAction,
  type FileTreeDir,
  type FileTreeEntry,
} from "./fileTree";

const f = (path: string, size = 1): WorkspaceFile => ({ path, size });

/** Compact shape for assertions: dirs as `name/`, files as `name`. */
function shape(entry: FileTreeEntry): string {
  return entry.kind === "dir" ? `${entry.name}/` : entry.name;
}

function names(dir: FileTreeDir): string[] {
  return dir.children.map(shape);
}

describe("buildFileTree", () => {
  it("空列表 → 只有空根目录", () => {
    const root = buildFileTree([]);
    expect(root.kind).toBe("dir");
    expect(root.path).toBe("");
    expect(root.children).toEqual([]);
  });

  it("根目录文件与嵌套目录同层呈现，文件保留 size", () => {
    const root = buildFileTree([f("run.log", 12), f("reports/a.md", 34)]);
    expect(names(root)).toEqual(["reports/", "run.log"]);
    const reports = root.children[0] as FileTreeDir;
    expect(reports.path).toBe("reports");
    expect(reports.children).toHaveLength(1);
    expect(reports.children[0]).toMatchObject({
      kind: "file",
      name: "a.md",
      path: "reports/a.md",
      size: 34,
    });
  });

  it("多级路径逐段建目录，深路径的 name/path 正确", () => {
    const root = buildFileTree([f("a/b/c.txt")]);
    const a = root.children[0] as FileTreeDir;
    const b = a.children[0] as FileTreeDir;
    expect([a.name, a.path]).toEqual(["a", "a"]);
    expect([b.name, b.path]).toEqual(["b", "a/b"]);
    expect(b.children[0]).toMatchObject({ kind: "file", path: "a/b/c.txt" });
  });

  it("同目录多文件合并，目录优先、名称自然排序", () => {
    const root = buildFileTree([
      f("zeta.txt"),
      f("Alpha.txt"),
      f("file10.txt"),
      f("file2.txt"),
      f("docs/b.md"),
      f("a10/y.txt"),
      f("a9/y.txt"),
      f("a/x.txt"),
    ]);
    expect(names(root)).toEqual([
      "a/",
      "a9/",
      "a10/",
      "docs/",
      "Alpha.txt",
      "file2.txt",
      "file10.txt",
      "zeta.txt",
    ]);
  });
});

describe("fileTreeRows", () => {
  const root = buildFileTree([
    f("a/b/c.txt", 3),
    f("a/d.txt", 4),
    f("e/f.txt", 5),
    f("run.log", 6),
  ]);

  it("默认只显示根层：目录折叠、根文件可见", () => {
    const rows = fileTreeRows(root, new Set());
    expect(rows.map((r) => `${r.depth}:${r.name}`)).toEqual([
      "0:a",
      "0:e",
      "0:run.log",
    ]);
    expect(rows[0]).toMatchObject({ kind: "dir", path: "a", expanded: false });
    expect(rows[2]).toMatchObject({ kind: "file", path: "run.log", size: 6 });
  });

  it("展开目录后按层显示子项，深度逐级递增", () => {
    const rows = fileTreeRows(root, new Set(["a", "a/b"]));
    expect(rows.map((r) => `${r.depth}:${r.name}`)).toEqual([
      "0:a",
      "1:b",
      "2:c.txt",
      "1:d.txt",
      "0:e",
      "0:run.log",
    ]);
    expect(rows[1]).toMatchObject({ path: "a/b", expanded: true });
    expect(rows[3]).toMatchObject({ path: "a/d.txt", expanded: false });
  });

  it("折叠后不再显示后代；未知展开路径不生效", () => {
    const rows = fileTreeRows(root, new Set(["nope"]));
    expect(rows.map((r) => r.path)).toEqual(["a", "e", "run.log"]);
  });
});

describe("fileTreeRows 过滤", () => {
  const root = buildFileTree([
    f("a/b/c.txt"),
    f("a/d.txt"),
    f("e/f.txt"),
    f("run.log"),
  ]);

  it("按文件名命中，祖先目录自动展开且只显示命中分支", () => {
    const rows = fileTreeRows(root, new Set(), "d.txt");
    expect(rows.map((r) => `${r.depth}:${r.name}`)).toEqual([
      "0:a",
      "1:d.txt",
    ]);
    expect(rows[0]).toMatchObject({ kind: "dir", expanded: true });
  });

  it("按路径命中时逐级展开祖先", () => {
    const rows = fileTreeRows(root, new Set(), "a/b");
    expect(rows.map((r) => `${r.depth}:${r.path}`)).toEqual([
      "0:a",
      "1:a/b",
      "2:a/b/c.txt",
    ]);
  });

  it("忽略大小写与首尾空白；无命中返回空", () => {
    expect(fileTreeRows(root, new Set(), "  RUN  ").map((r) => r.path)).toEqual([
      "run.log",
    ]);
    expect(fileTreeRows(root, new Set(), "missing")).toEqual([]);
  });

  it("过滤不修改传入的展开集合；清空后恢复原展开状态", () => {
    const expanded = new Set(["e"]);
    fileTreeRows(root, expanded, "c.txt");
    expect([...expanded]).toEqual(["e"]);
    expect(fileTreeRows(root, expanded, "").map((r) => r.path)).toEqual([
      "a",
      "e",
      "e/f.txt",
      "run.log",
    ]);
  });
});

describe("展开状态", () => {
  it("ancestorPaths 列出目录祖先，根文件返回空", () => {
    expect(ancestorPaths("a/b/c.txt")).toEqual(["a", "a/b"]);
    expect(ancestorPaths("run.log")).toEqual([]);
  });

  it("expandAncestors 展开当前文件的全部祖先，不改原集合", () => {
    const before = new Set(["e"]);
    const after = expandAncestors(before, "a/b/c.txt");
    expect([...after].sort()).toEqual(["a", "a/b", "e"]);
    expect([...before]).toEqual(["e"]);
    expect(expandAncestors(after, "a/b/c.txt")).toEqual(after);
  });

  it("toggleDir 来回切换单个目录", () => {
    const opened = toggleDir(new Set(), "a");
    expect(opened.has("a")).toBe(true);
    expect(toggleDir(opened, "a").has("a")).toBe(false);
  });
});

describe("treeKeyAction", () => {
  const root = buildFileTree([
    f("a/b/c.txt"),
    f("a/d.txt"),
    f("e/f.txt"),
    f("run.log"),
  ]);
  const expanded = new Set(["a"]);
  const rows = fileTreeRows(root, expanded);

  const at = (key: Parameters<typeof treeKeyAction>[2], path: string) =>
    treeKeyAction(rows, path, key);

  it("上下移动按可见行前进，边界不移动", () => {
    expect(at("ArrowDown", "a")).toEqual({ focusPath: "a/b", toggle: null });
    expect(at("ArrowDown", "e")).toEqual({ focusPath: "run.log", toggle: null });
    expect(at("ArrowDown", "run.log")).toEqual({ focusPath: null, toggle: null });
    expect(at("ArrowUp", "a/b")).toEqual({ focusPath: "a", toggle: null });
    expect(at("ArrowUp", "a")).toEqual({ focusPath: null, toggle: null });
  });

  it("Home/End 到首尾行", () => {
    expect(at("Home", "e")).toEqual({ focusPath: "a", toggle: null });
    expect(at("End", "a")).toEqual({ focusPath: "run.log", toggle: null });
  });

  it("右方向键：折叠目录先展开，已展开则进入首个子项；文件不动", () => {
    expect(at("ArrowRight", "a/b")).toEqual({
      focusPath: null,
      toggle: "a/b",
    });
    expect(at("ArrowRight", "a")).toEqual({ focusPath: "a/b", toggle: null });
    expect(at("ArrowRight", "run.log")).toEqual({ focusPath: null, toggle: null });
  });

  it("左方向键：展开目录先折叠，否则回到父目录；根层不动", () => {
    expect(at("ArrowLeft", "a")).toEqual({
      focusPath: null,
      toggle: "a",
    });
    expect(at("ArrowLeft", "a/d.txt")).toEqual({ focusPath: "a", toggle: null });
    expect(at("ArrowLeft", "e")).toEqual({ focusPath: null, toggle: null });
  });
});

describe("isTreeKey", () => {
  it("只把导航键交给树键盘转移", () => {
    for (const key of ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"]) {
      expect(isTreeKey(key)).toBe(true);
    }
    expect(isTreeKey("Enter")).toBe(false);
    expect(isTreeKey("Tab")).toBe(false);
  });
});

describe("轮询后的树重建", () => {
  it("同一展开集合在新树上保持展开，新增文件出现在正确位置", () => {
    const before = buildFileTree([f("a/b/c.txt")]);
    const expanded = expandAncestors(new Set(), "a/b/c.txt");
    const after = buildFileTree([f("a/b/c.txt"), f("a/new.txt"), f("z.txt")]);
    expect(fileTreeRows(after, expanded).map((r) => r.path)).toEqual([
      "a",
      "a/b",
      "a/b/c.txt",
      "a/new.txt",
      "z.txt",
    ]);
  });
});
