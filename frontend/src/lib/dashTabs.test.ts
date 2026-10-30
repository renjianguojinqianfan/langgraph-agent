import { describe, expect, it } from "vitest";
import {
  closeFileTab,
  fileTabId,
  fileTabLabel,
  fileTabPath,
  openFileTab,
  tabAfterClosing,
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
