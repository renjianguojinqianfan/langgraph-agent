import { describe, expect, it } from "vitest";
import { inputNeedsExpand, TOOL_INPUT_PREVIEW_LIMIT } from "./toolInput";

describe("inputNeedsExpand (#258 AC2)", () => {
  it("uses an 800-code-point inclusive threshold", () => {
    expect(TOOL_INPUT_PREVIEW_LIMIT).toBe(800);
    expect(inputNeedsExpand("a".repeat(800))).toBe(false);
    expect(inputNeedsExpand("a".repeat(801))).toBe(true);
  });

  it("empty input never needs expanding", () => {
    expect(inputNeedsExpand("")).toBe(false);
  });

  it("counts code points, not UTF-16 units (CJK / emoji)", () => {
    expect(inputNeedsExpand("😀".repeat(800))).toBe(false);
    expect(inputNeedsExpand("😀".repeat(801))).toBe(true);
    expect(inputNeedsExpand("哈".repeat(801))).toBe(true);
  });

  it("honours an explicit limit", () => {
    expect(inputNeedsExpand("12345", 5)).toBe(false);
    expect(inputNeedsExpand("123456", 5)).toBe(true);
  });
});