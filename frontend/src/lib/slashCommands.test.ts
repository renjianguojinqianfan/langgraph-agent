import { describe, expect, it } from "vitest";
import type { Task, TaskMode, TaskStatus } from "../types";
import {
  buildMenuEntries,
  firstEnabledIndex,
  menuEntryDescendantId,
  menuEntryEnabled,
  moveSelection,
  removeCommandToken,
  slashMenuState,
  slashTokenAt,
  type SlashCommandContext,
} from "./slashCommands";

function task(status: TaskStatus, mode: TaskMode = "build"): Task {
  return {
    id: "t1",
    title: "t1",
    user_input: "",
    workspace: "E:\\w",
    model: "",
    status,
    mode,
    steps: [],
    plan_version: 0,
    conversation: [],
    final_answer: "",
    created_at: "2026-10-28T00:00:00Z",
    updated_at: "2026-10-28T00:00:00Z",
  };
}

const ctx = (over: Partial<SlashCommandContext> = {}): SlashCommandContext => ({
  task: undefined,
  mode: "build",
  hasModels: true,
  ...over,
});

const names = (items: ReturnType<typeof slashMenuState>["items"]) =>
  items.map((item) => item.name);

describe("slashTokenAt / #342 trigger", () => {
  it("ignores empty text and a caret with no slash before it", () => {
    expect(slashTokenAt("", 0)).toBeNull();
    expect(slashTokenAt("hello", 5)).toBeNull();
  });

  it("triggers at the start and reports the whole whitespace token", () => {
    expect(slashTokenAt("/", 1)).toEqual({ start: 0, end: 1, query: "" });
    expect(slashTokenAt("/new", 4)).toEqual({ start: 0, end: 4, query: "new" });
  });

  it("triggers after whitespace (space and newline)", () => {
    expect(slashTokenAt("hello /ne", 9)).toEqual({
      start: 6,
      end: 9,
      query: "ne",
    });
    expect(slashTokenAt("line\n/help", 10)).toEqual({
      start: 5,
      end: 10,
      query: "help",
    });
  });

  it("triggers after punctuation but not after word characters", () => {
    expect(slashTokenAt("你好，/p", 5)).toEqual({
      start: 3,
      end: 5,
      query: "p",
    });
    expect(slashTokenAt("(/new", 5)).toEqual({
      start: 1,
      end: 5,
      query: "new",
    });
    expect(slashTokenAt("foo/bar", 7)).toBeNull();
  });

  it("does not trigger for URLs, filesystem paths or mid-word slashes", () => {
    expect(slashTokenAt("https://example.com", 19)).toBeNull();
    expect(slashTokenAt("https://", 8)).toBeNull();
    expect(slashTokenAt("src/main.ts", 11)).toBeNull();
    expect(slashTokenAt("./src", 5)).toBeNull();
    expect(slashTokenAt("/usr/bin", 8)).toBeNull();
    expect(slashTokenAt("C:/Users/me", 11)).toBeNull();
  });

  it("only opens for the token at the caret", () => {
    expect(slashTokenAt("/new hello", 10)).toBeNull();
    expect(slashTokenAt("/new hello", 5)).toBeNull();
    expect(slashTokenAt("hello /new", 0)).toBeNull();
  });

  it("uses the caret inside a token and keeps the full token end", () => {
    // Caret sits between "w" and "x": the menu still filters on "new" and a
    // command execution removes the whole "/newx" token.
    expect(slashTokenAt("/newx", 4)).toEqual({
      start: 0,
      end: 5,
      query: "new",
    });
    expect(slashTokenAt("hello /newx", 10)).toEqual({
      start: 6,
      end: 11,
      query: "new",
    });
  });

  it("clamps an out-of-range caret instead of throwing", () => {
    expect(slashTokenAt("/new", 99)).toEqual({
      start: 0,
      end: 4,
      query: "new",
    });
    expect(slashTokenAt("/new", -1)).toBeNull();
  });
});

describe("slashMenuState / filtering (#342)", () => {
  it("lists all eight built-in commands for a bare slash", () => {
    const state = slashMenuState("/", 1, ctx());
    expect(state.open).toBe(true);
    expect(names(state.items)).toEqual([
      "new",
      "stop",
      "plan",
      "build",
      "model",
      "resume",
      "tasks",
      "help",
    ]);
  });

  it("filters by prefix, case-insensitively", () => {
    expect(names(slashMenuState("/n", 2, ctx()).items)).toEqual(["new"]);
    expect(names(slashMenuState("/NE", 3, ctx()).items)).toEqual(["new"]);
    expect(names(slashMenuState("/mo", 3, ctx()).items)).toEqual(["model"]);
    // Prefix only: "stop" is not offered for "top".
    expect(names(slashMenuState("/top", 4, ctx()).items)).toEqual([]);
  });

  it("keeps the menu open with no items when nothing matches", () => {
    const state = slashMenuState("/zzz", 4, ctx());
    expect(state.open).toBe(true);
    expect(state.items).toEqual([]);
  });

  it("closes when the caret leaves the slash token", () => {
    expect(slashMenuState("/new hello", 10, ctx()).open).toBe(false);
  });
});

describe("command availability (#342)", () => {
  const item = (state: ReturnType<typeof slashMenuState>, name: string) => {
    const found = state.items.find((entry) => entry.name === name);
    if (!found) throw new Error(`missing command ${name}`);
    return found;
  };

  it("with no task: new/model/tasks/help/plan are available, stop/resume are not", () => {
    const state = slashMenuState("/", 1, ctx());
    expect(item(state, "new").enabled).toBe(true);
    expect(item(state, "model").enabled).toBe(true);
    expect(item(state, "tasks").enabled).toBe(true);
    expect(item(state, "help").enabled).toBe(true);
    expect(item(state, "plan").enabled).toBe(true);
    expect(item(state, "build").enabled).toBe(false);
    expect(item(state, "build").reason).toBe("当前任务已在执行模式");
    expect(item(state, "stop").enabled).toBe(false);
    expect(item(state, "stop").reason).toBe("没有运行中的任务");
    expect(item(state, "resume").enabled).toBe(false);
    expect(item(state, "resume").reason).toBe("没有可继续的任务");
  });

  it("a RUNNING task enables stop but not resume", () => {
    const state = slashMenuState("/", 1, ctx({ task: task("RUNNING") }));
    expect(item(state, "stop").enabled).toBe(true);
    expect(item(state, "stop").reason).toBeNull();
    expect(item(state, "resume").enabled).toBe(false);
    expect(item(state, "resume").reason).toBe("任务仍在运行");
  });

  it("a PENDING task is stoppable", () => {
    const state = slashMenuState("/", 1, ctx({ task: task("PENDING") }));
    expect(item(state, "stop").enabled).toBe(true);
  });

  it("a parked task enables resume and disables stop", () => {
    const state = slashMenuState(
      "/",
      1,
      ctx({ task: task("INTERRUPTED") })
    );
    expect(item(state, "resume").enabled).toBe(true);
    expect(item(state, "stop").enabled).toBe(false);
    expect(item(state, "stop").reason).toBe("没有运行中的任务");
  });

  it("disables the mode the task is already in", () => {
    const plan = slashMenuState("/", 1, ctx({ task: task("INTERRUPTED", "plan") }));
    expect(item(plan, "plan").enabled).toBe(false);
    expect(item(plan, "plan").reason).toBe("当前任务已在计划模式");
    expect(item(plan, "build").enabled).toBe(true);
  });

  it("disables model when the catalog has no selectable models", () => {
    const state = slashMenuState("/", 1, ctx({ hasModels: false }));
    expect(item(state, "model").enabled).toBe(false);
    expect(item(state, "model").reason).toBe("没有可选模型");
  });

  it("gives every command a label and a description", () => {
    for (const entry of slashMenuState("/", 1, ctx()).items) {
      expect(entry.label).toBe(`/${entry.name}`);
      expect(entry.description.length).toBeGreaterThan(0);
    }
  });
});

describe("selection helpers (#342 keyboard)", () => {
  it("starts on the first enabled command", () => {
    expect(firstEnabledIndex(slashMenuState("/", 1, ctx()).items)).toBe(0);
    const intro = [
      { enabled: false },
      { enabled: true },
      { enabled: true },
    ];
    expect(firstEnabledIndex(intro as never)).toBe(1);
    expect(firstEnabledIndex([])).toBe(-1);
    expect(firstEnabledIndex([{ enabled: false }] as never)).toBe(0);
  });

  it("wraps ↑/↓ around the item count", () => {
    expect(moveSelection(0, 1, 3)).toBe(1);
    expect(moveSelection(2, 1, 3)).toBe(0);
    expect(moveSelection(0, -1, 3)).toBe(2);
    expect(moveSelection(-1, 1, 3)).toBe(0);
    expect(moveSelection(0, 1, 0)).toBe(-1);
  });
});

describe("removeCommandToken (#342: execute clears the token, keeps the draft)", () => {
  it("clears a lone command", () => {
    const token = slashTokenAt("/new", 4)!;
    expect(removeCommandToken("/new", token)).toBe("");
  });

  it("removes the token and one joining space on either side", () => {
    expect(
      removeCommandToken("hello /new", slashTokenAt("hello /new", 10)!)
    ).toBe("hello");
    expect(
      removeCommandToken("/new hello", slashTokenAt("/new hello", 4)!)
    ).toBe("hello");
    expect(
      removeCommandToken("hello /new world", slashTokenAt("hello /new world", 10)!)
    ).toBe("hello world");
  });

  it("leaves attached punctuation and the rest of the draft alone", () => {
    expect(
      removeCommandToken("foo,/new", slashTokenAt("foo,/new", 8)!)
    ).toBe("foo,");
    expect(
      removeCommandToken("/new! rest", slashTokenAt("/new! rest", 4)!)
    ).toBe("! rest");
  });

  it("removes the full whitespace token even when the caret is mid-token", () => {
    expect(
      removeCommandToken("/newx", slashTokenAt("/newx", 4)!)
    ).toBe("");
  });
});

describe("combined menu entries", () => {
  const commands = [
    { id: "new" as const, enabled: true },
    { id: "stop" as const, enabled: false },
  ];
  const skills = [{ name: "alpha" }, { name: "beta" }];

  it("keeps commands, then skills, then the retry row", () => {
    expect(buildMenuEntries(commands, skills, true)).toEqual([
      { kind: "command", id: "new", enabled: true },
      { kind: "command", id: "stop", enabled: false },
      { kind: "skill", name: "alpha", index: 0 },
      { kind: "skill", name: "beta", index: 1 },
      { kind: "retry" },
    ]);
  });

  it("marks only disabled commands as unavailable", () => {
    const entries = buildMenuEntries(commands, skills, false);
    expect(entries.map(menuEntryEnabled)).toEqual([true, false, true, true]);
  });

  it("derives one activedescendant id per entry kind", () => {
    const entries = buildMenuEntries(commands, skills, true);
    expect(entries.map((entry) => menuEntryDescendantId(entry, "menu"))).toEqual([
      "menu-new",
      "menu-stop",
      "menu-skill-0",
      "menu-skill-1",
      "menu-skills-retry",
    ]);
  });
});
