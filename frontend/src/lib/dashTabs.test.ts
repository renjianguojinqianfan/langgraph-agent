import { describe, expect, it } from "vitest";
import {
  changeTabId,
  changeTabRef,
  closeFileTab,
  fileTabId,
  fileTabLabel,
  fileTabPath,
  isClosableTab,
  openChangeTab,
  openFileTab,
  tabAfterClosing,
  tabAfterClosingChange,
} from "./dashTabs";

describe("file tab ids", () => {
  it("round-trips a path and never collides with a fixed pane", () => {
    const tab = fileTabId("src/App.tsx");
    expect(tab).toBe("file:src/App.tsx");
    expect(fileTabPath(tab)).toBe("src/App.tsx");
    expect(fileTabPath("files")).toBeNull();
  });
});

describe("fileTabLabel", () => {
  it("uses the basename when it is unique", () => {
    expect(fileTabLabel("src/App.tsx", ["src/App.tsx", "README.md"])).toBe("App.tsx");
  });

  it("falls back to the full path for duplicate basenames", () => {
    const opened = ["a/README.md", "b/README.md"];
    expect(fileTabLabel("a/README.md", opened)).toBe("a/README.md");
    expect(fileTabLabel("b/README.md", opened)).toBe("b/README.md");
  });
});

describe("openFileTab", () => {
  it("keeps creation order and reuses an already open file", () => {
    const opened = openFileTab(["a.txt"], "b.txt");
    expect(opened).toEqual(["a.txt", "b.txt"]);
    expect(openFileTab(opened, "a.txt")).toEqual(["a.txt", "b.txt"]);
  });
});

describe("closeFileTab / tabAfterClosing", () => {
  const opened = ["a.txt", "b.txt", "c.txt"];

  it("removes only the closed path", () => {
    expect(closeFileTab(opened, "b.txt")).toEqual(["a.txt", "c.txt"]);
  });

  it("closing the active tab activates its left neighbour", () => {
    expect(tabAfterClosing(opened, "c.txt", fileTabId("c.txt"))).toBe(
      fileTabId("b.txt")
    );
  });

  it("falls back to the right neighbour when no left one remains", () => {
    expect(tabAfterClosing(opened, "a.txt", fileTabId("a.txt"))).toBe(
      fileTabId("b.txt")
    );
  });

  it("falls back to the file list when the last tab closes", () => {
    expect(tabAfterClosing(["a.txt"], "a.txt", fileTabId("a.txt"))).toBe("files");
  });

  it("leaves an unrelated active tab alone", () => {
    expect(tabAfterClosing(opened, "a.txt", "plan")).toBe("plan");
    expect(tabAfterClosing(opened, "a.txt", fileTabId("b.txt"))).toBe(
      fileTabId("b.txt")
    );
  });
});

describe("change review tab ids", () => {
  it("round-trips (task, round) and never collides with a file tab", () => {
    const tab = changeTabId("task-1", 2);
    expect(tab).toBe("change:task-1:2");
    expect(changeTabRef(tab)).toEqual({ taskId: "task-1", round: 2 });
    expect(changeTabRef("file:a.ts")).toBeNull();
    expect(changeTabRef("changes")).toBeNull();
  });

  it("reuses one tab per (task, round) and keeps creation order", () => {
    const opened = openChangeTab([], 1);
    expect(opened).toEqual([1]);
    expect(openChangeTab(opened, 1)).toEqual([1]);
    expect(openChangeTab(opened, 2)).toEqual([1, 2]);
  });

  it("closing the active review tab picks a neighbour or the file list", () => {
    expect(tabAfterClosingChange([1, 2], 2, changeTabId("t", 2), "t")).toBe(
      changeTabId("t", 1)
    );
    expect(tabAfterClosingChange([1], 1, changeTabId("t", 1), "t")).toBe("files");
    expect(tabAfterClosingChange([1, 2], 1, "plan", "t")).toBe("plan");
  });
});

describe("isClosableTab", () => {
  it("is true only for opened files and review rounds", () => {
    expect(isClosableTab("files")).toBe(false);
    expect(isClosableTab("plan")).toBe(false);
    expect(isClosableTab("events")).toBe(false);
    expect(isClosableTab(fileTabId("src/App.tsx"))).toBe(true);
    expect(isClosableTab(changeTabId("task-1", 2))).toBe(true);
  });
});
