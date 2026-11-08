import { describe, expect, it } from "vitest";
import type { StepRecord } from "../types";
import { stepHasReasoning } from "./reasoning";

const step = (overrides: Partial<StepRecord> = {}): StepRecord => ({
  index: 1,
  thought: "答",
  tool_calls: [],
  status: "done",
  ...overrides,
});

describe("stepHasReasoning (D-40: no empty thinking block)", () => {
  it("is false for records without the field or with blank content", () => {
    expect(stepHasReasoning(step())).toBe(false);
    expect(stepHasReasoning(step({ reasoning: "" }))).toBe(false);
    expect(stepHasReasoning(step({ reasoning: "   \n " }))).toBe(false);
  });

  it("is true only for non-empty reasoning", () => {
    expect(stepHasReasoning(step({ reasoning: "先读文件再回答" }))).toBe(true);
    expect(stepHasReasoning(step({ reasoning: "\n  想一下" }))).toBe(true);
  });
});
