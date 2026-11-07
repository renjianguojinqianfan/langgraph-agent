import { describe, expect, it } from "vitest";
import {
  LEFT_DRAWER_MAX,
  LEFT_DRAWER_QUERY,
  RIGHT_DRAWER_MAX,
  RIGHT_DRAWER_QUERY,
} from "./breakpoints";

// D-25 判据：断点常量与 media query 字符串逐字断言。`<` 语义与 Tailwind v4
// `max-[860px]` / `max-[1180px]` 编译出的 `not all and (min-width: X)` 同界，
// 恰好 860 / 1180 仍算宽屏。
describe("drawer breakpoints (D-25)", () => {
  it("left drawer: 860, `<` semantics", () => {
    expect(LEFT_DRAWER_MAX).toBe(860);
    expect(LEFT_DRAWER_QUERY).toBe("(width < 860px)");
  });

  it("right drawer: 1180, `<` semantics", () => {
    expect(RIGHT_DRAWER_MAX).toBe(1180);
    expect(RIGHT_DRAWER_QUERY).toBe("(width < 1180px)");
  });
});
