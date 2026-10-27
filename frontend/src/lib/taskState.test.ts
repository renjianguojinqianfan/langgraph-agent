import { describe, expect, it } from "vitest";
import { shouldPollFiles } from "./taskState";

describe("shouldPollFiles", () => {
  it("可见 + 运行中 → 继续 2s 轮询", () => {
    expect(shouldPollFiles("RUNNING", true)).toBe(true);
  });

  // #230 的核心：抽屉关掉时面板不可见，任务还在跑也不能轮询。
  it("不可见 + 运行中 → 不轮询", () => {
    expect(shouldPollFiles("RUNNING", false)).toBe(false);
  });

  it("可见但任务已不在跑 → 不轮询（保持既有行为）", () => {
    expect(shouldPollFiles("PENDING", true)).toBe(false);
    expect(shouldPollFiles("INTERRUPTED", true)).toBe(false);
    expect(shouldPollFiles("FAILED", true)).toBe(false);
  });
});
