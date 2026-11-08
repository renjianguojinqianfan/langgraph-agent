import { describe, expect, it } from "vitest";
import { FINAL_ANSWER_SUMMARY, stepSummary } from "./stepSummary";

// #287: the final-answer step's row summary must not repeat the answer body.

const step = (thought: string, tools: string[] = []) => ({
  thought,
  tool_calls: tools.map((tool_name) => ({ tool_name })),
});

describe("stepSummary (#287 重复回答修复)", () => {
  it("命中本轮答案（trim 后相等）→ 「最终回答」", () => {
    expect(stepSummary(step("这是最终回答"), ["这是最终回答"])).toBe(FINAL_ANSWER_SUMMARY);
    expect(stepSummary(step("  这是最终回答\n"), ["这是最终回答"])).toBe(FINAL_ANSWER_SUMMARY);
  });

  it("普通 thought 原样作为摘要", () => {
    expect(stepSummary(step("先读文件再回答"), ["最终回答"])).toBe("先读文件再回答");
  });

  it("与答案只是前缀/部分相同不算命中", () => {
    expect(stepSummary(step("先读文件"), ["先读文件再回答"])).toBe("先读文件");
  });

  it("无 thought → 工具名 join('、')", () => {
    expect(stepSummary(step("", ["read_file", "bash"]))).toBe("read_file、bash");
  });

  it("空 thought 且无工具 → 「步骤」", () => {
    expect(stepSummary(step("   "), [])).toBe("步骤");
    expect(stepSummary(step(""))).toBe("步骤");
  });

  it("答案列表为空时按普通 thought 显示", () => {
    expect(stepSummary(step("答案原文"), [])).toBe("答案原文");
  });
});
