import { describe, expect, it } from "vitest";
import { Markdown } from "./markdown";

/** 结构守卫（D-30 / #258 AC4）：判据 = 去掉 `memo(...)` 包装本测试即红；
 * 不是渲染测试——Node 环境没有 DOM，只核 React 的 exotic 标记。 */
describe("Markdown memo guard", () => {
  it("is a memo component, not a plain function component", () => {
    expect((Markdown as unknown as { $$typeof: symbol }).$$typeof).toBe(
      Symbol.for("react.memo")
    );
  });
});