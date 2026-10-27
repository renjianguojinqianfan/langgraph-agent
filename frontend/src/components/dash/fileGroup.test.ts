import { describe, expect, it } from "vitest";
import { WorkspaceFile } from "../../types";
import { fileName, groupFiles } from "./fileGroup";

const f = (path: string, size = 1): WorkspaceFile => ({ path, size });

describe("groupFiles", () => {
  it("按父目录分组，首见顺序保留", () => {
    const groups = groupFiles([
      f("reports/a.md"),
      f("pipeline/consumer.py"),
      f("reports/b.md"),
      f("run.log"),
    ]);
    expect(groups.map((g) => g.label)).toEqual(["reports", "pipeline", "根目录"]);
    expect(groups[0].files.map((x) => x.path)).toEqual([
      "reports/a.md",
      "reports/b.md",
    ]);
  });

  it("多级目录按完整父路径单列，组头取 basename", () => {
    const groups = groupFiles([f("a/b/c.txt"), f("a/b/d.txt"), f("a/e.txt")]);
    expect(groups.map((g) => [g.dir, g.label])).toEqual([
      ["a/b", "b"],
      ["a", "a"],
    ]);
  });

  it("以斜杠开头的路径不会被当成根目录文件", () => {
    const groups = groupFiles([f("/abs/x.txt")]);
    expect(groups[0].dir).toBe("/abs");
    expect(groups[0].label).toBe("abs");
  });

  it("空列表 → 空分组", () => {
    expect(groupFiles([])).toEqual([]);
  });
});

describe("fileName", () => {
  it("取路径末段", () => {
    expect(fileName("reports/pipeline-bottlenecks.md")).toBe(
      "pipeline-bottlenecks.md"
    );
    expect(fileName("run.log")).toBe("run.log");
  });
});
