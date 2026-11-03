import { describe, expect, it } from "vitest";
import { withDirParam } from "./workspace";

describe("withDirParam", () => {
  it("sets the directory as an encoded query parameter", () => {
    expect(withDirParam("", "E:\\example\\project")).toBe(
      `?dir=${encodeURIComponent("E:\\example\\project")}`
    );
  });

  it("replaces an existing directory and keeps other parameters", () => {
    expect(withDirParam("?dir=old&x=1", "new")).toBe("?dir=new&x=1");
  });

  it("removes the parameter for an empty/blank directory", () => {
    expect(withDirParam("?dir=old", "")).toBe("");
    expect(withDirParam("?dir=old", "   ")).toBe("");
  });
});
