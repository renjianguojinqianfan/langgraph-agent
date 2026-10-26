import { describe, expect, it } from "vitest";
import { shouldSendOnEnter } from "./keys";

describe("shouldSendOnEnter", () => {
  it("Enter 发送", () => {
    expect(shouldSendOnEnter("Enter", false, false)).toBe(true);
  });

  it("Shift+Enter 换行，不发送", () => {
    expect(shouldSendOnEnter("Enter", true, false)).toBe(false);
  });

  it("输入法组字中的 Enter 不发送", () => {
    expect(shouldSendOnEnter("Enter", false, true)).toBe(false);
  });

  it("其他按键不发送", () => {
    expect(shouldSendOnEnter("a", false, false)).toBe(false);
  });
});
