import { describe, expect, it } from "vitest";
import { addRecentWorkspace } from "./workspaces";

describe("addRecentWorkspace", () => {
  it("puts the newest path first", () => {
    expect(addRecentWorkspace(["old"], "new")).toEqual(["new", "old"]);
  });

  it("dedupes an existing path and moves it to the front", () => {
    expect(addRecentWorkspace(["a", "b", "c"], "b")).toEqual(["b", "a", "c"]);
  });

  it("caps the list at the limit", () => {
    const recents = Array.from({ length: 8 }, (_, index) => `p${index}`);
    expect(addRecentWorkspace(recents, "new", 8)).toHaveLength(8);
    expect(addRecentWorkspace(recents, "new", 8)[7]).toBe("p6");
  });

  it("ignores a blank path", () => {
    expect(addRecentWorkspace(["a"], "   ")).toEqual(["a"]);
  });
});
