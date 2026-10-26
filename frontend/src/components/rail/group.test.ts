import { describe, expect, it } from "vitest";
import type { Task } from "../../types";
import {
  basename,
  filterByQuery,
  groupByWorkspace,
  railGroups,
  workspaceName,
} from "./group";

function task(id: string, workspace: string, overrides: Partial<Task> = {}): Task {
  return {
    id,
    title: `任务 ${id}`,
    user_input: `输入 ${id}`,
    workspace,
    model: "",
    status: "INTERRUPTED",
    mode: "build",
    steps: [],
    plan_version: 0,
    conversation: [],
    final_answer: "",
    error: null,
    created_at: "2026-10-27T00:00:00",
    updated_at: "2026-10-27T00:00:00",
    ...overrides,
  };
}

describe("basename", () => {
  it("handles posix and windows separators", () => {
    expect(basename("E:/code/demo/langgraph-agent")).toBe("langgraph-agent");
    expect(basename("E:\\code\\demo\\langgraph-agent")).toBe("langgraph-agent");
  });

  it("drops trailing separators and handles empty paths", () => {
    expect(basename("E:/code/demo/")).toBe("demo");
    expect(basename("E:\\code\\demo\\")).toBe("demo");
    expect(basename("/")).toBe("/");
    expect(basename("   ")).toBe("");
  });

  it("falls back to 默认目录 for the default workspace", () => {
    expect(workspaceName("")).toBe("默认目录");
    expect(workspaceName("E:/work/project")).toBe("project");
  });
});

describe("groupByWorkspace", () => {
  it("groups in first-seen order and keeps row order inside a group", () => {
    const a = task("a", "E:/work/alpha");
    const b = task("b", "E:/work/beta");
    const c = task("c", "E:/work/alpha");
    const groups = groupByWorkspace([a, b, c]);

    expect(groups.map((g) => g.label)).toEqual(["alpha", "beta"]);
    expect(groups[0].tasks.map((t) => t.id)).toEqual(["a", "c"]);
    expect(groups[1].tasks.map((t) => t.id)).toEqual(["b"]);
  });

  it("collects tasks without a workspace under 默认目录", () => {
    const groups = groupByWorkspace([task("a", ""), task("b", "  ")]);

    expect(groups).toHaveLength(1);
    expect(groups[0].workspace).toBe("");
    expect(groups[0].label).toBe("默认目录");
    expect(groups[0].tasks.map((t) => t.id)).toEqual(["a", "b"]);
  });
});

describe("railGroups", () => {
  const tasks = [task("a", "E:/work/alpha"), task("b", "E:/work/beta")];

  it("keeps 本目录 as one ungrouped list", () => {
    const groups = railGroups(tasks, "current");

    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("");
    expect(groups[0].tasks).toEqual(tasks);
  });

  it("splits 全部 by workspace", () => {
    const groups = railGroups(tasks, "all");

    expect(groups.map((g) => g.label)).toEqual(["alpha", "beta"]);
  });
});

describe("filterByQuery", () => {
  const tasks = [
    task("a", "E:/work/alpha", { title: "Refactor header", user_input: "整理顶栏" }),
    task("b", "E:/work/beta", { title: "写测试", user_input: "cover the rail" }),
  ];

  it("returns the loaded list unchanged for a blank query", () => {
    expect(filterByQuery(tasks, "   ")).toBe(tasks);
  });

  it("matches title, input and workspace case-insensitively", () => {
    expect(filterByQuery(tasks, "REFACTOR").map((t) => t.id)).toEqual(["a"]);
    expect(filterByQuery(tasks, "顶栏").map((t) => t.id)).toEqual(["a"]);
    expect(filterByQuery(tasks, "beta").map((t) => t.id)).toEqual(["b"]);
  });

  it("returns nothing when no field matches", () => {
    expect(filterByQuery(tasks, "missing")).toEqual([]);
  });
});
