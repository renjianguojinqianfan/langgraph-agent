import { describe, expect, it } from "vitest";
import {
  clampNavigatorWidth,
  navigatorKeyWidth,
  navigatorLayout,
} from "./navigatorLayout";

describe("navigatorLayout 模式断点", () => {
  it("行宽 399 → fill，400 → float", () => {
    expect(navigatorLayout(399, 1024).mode).toBe("fill");
    expect(navigatorLayout(400, 1024).mode).toBe("float");
  });

  it("400–559 是 float，560 起是 side", () => {
    expect(navigatorLayout(420, 1024).mode).toBe("float");
    expect(navigatorLayout(559, 1024).mode).toBe("float");
    expect(navigatorLayout(560, 1024).mode).toBe("side");
  });

  it("行宽 420：视口 767 是 fill，768 是 float（视口与行宽两条规则都成立）", () => {
    expect(navigatorLayout(420, 767).mode).toBe("fill");
    expect(navigatorLayout(420, 768).mode).toBe("float");
  });

  it("视口 <768 压过更宽的行：行 1000 + 视口 767 → fill", () => {
    expect(navigatorLayout(1000, 767).mode).toBe("fill");
    expect(navigatorLayout(1000, 768).mode).toBe("side");
  });

  it("行宽 <400 压过更宽的视口：399 + 视口 1024 → fill", () => {
    expect(navigatorLayout(399, 1024).mode).toBe("fill");
  });

  it("fill 铺满整行且不可调整；float/side 默认可调整", () => {
    const fill = navigatorLayout(399, 1024);
    expect(fill).toMatchObject({
      resizable: false,
      minWidth: 399,
      maxWidth: 399,
      defaultWidth: 399,
    });
    expect(navigatorLayout(420, 1024).resizable).toBe(true);
    expect(navigatorLayout(560, 1024).resizable).toBe(true);
  });
});

describe("navigatorLayout 宽度 clamp", () => {
  it("420 float：默认 260、最小 180、最大 300", () => {
    const layout = navigatorLayout(420, 1024);
    expect(layout).toMatchObject({
      defaultWidth: 260,
      minWidth: 180,
      maxWidth: 300,
    });
  });

  it("400 float：最大为 row - 120 = 280", () => {
    expect(navigatorLayout(400, 1024)).toMatchObject({
      defaultWidth: 260,
      minWidth: 180,
      maxWidth: 280,
    });
  });

  it("559 float：最大仍是 300", () => {
    expect(navigatorLayout(559, 1024).maxWidth).toBe(300);
  });

  it("560 side：树最大 240，预览 body 至少 320", () => {
    const layout = navigatorLayout(560, 1024);
    expect(layout).toMatchObject({
      mode: "side",
      minWidth: 180,
      maxWidth: 240,
      defaultWidth: 240,
    });
    expect(560 - layout.maxWidth).toBeGreaterThanOrEqual(320);
  });

  it("600 side：最大 280、默认 260", () => {
    expect(navigatorLayout(600, 1024)).toMatchObject({
      maxWidth: 280,
      defaultWidth: 260,
    });
  });

  it("≥620 side：最大封顶 300", () => {
    expect(navigatorLayout(620, 1024).maxWidth).toBe(300);
    expect(navigatorLayout(1000, 1024).maxWidth).toBe(300);
  });
});

describe("clampNavigatorWidth", () => {
  const float = navigatorLayout(420, 1024);
  const side = navigatorLayout(560, 1024);

  it("区间内原样（取整），越界夹到 min / max", () => {
    expect(clampNavigatorWidth(240, float)).toBe(240);
    expect(clampNavigatorWidth(100, float)).toBe(180);
    expect(clampNavigatorWidth(999, float)).toBe(300);
    expect(clampNavigatorWidth(220.4, float)).toBe(220);
  });

  it("按各自模式的 max 夹：同一宽度在 560 side 被夹到 240", () => {
    expect(clampNavigatorWidth(300, side)).toBe(240);
    expect(clampNavigatorWidth(200, side)).toBe(200);
  });

  it("非有限值回退到该模式默认宽度", () => {
    expect(clampNavigatorWidth(Number.NaN, float)).toBe(260);
    expect(clampNavigatorWidth(Number.POSITIVE_INFINITY, side)).toBe(240);
  });
});

describe("navigatorKeyWidth", () => {
  const float = navigatorLayout(420, 1024);

  it("ArrowLeft / ArrowRight 按 16px 步进并夹紧", () => {
    expect(navigatorKeyWidth(260, "ArrowLeft", float)).toBe(244);
    expect(navigatorKeyWidth(260, "ArrowRight", float)).toBe(276);
    expect(navigatorKeyWidth(180, "ArrowLeft", float)).toBe(180);
    expect(navigatorKeyWidth(300, "ArrowRight", float)).toBe(300);
  });

  it("Home / End 到最小 / 最大", () => {
    expect(navigatorKeyWidth(260, "Home", float)).toBe(180);
    expect(navigatorKeyWidth(260, "End", float)).toBe(300);
  });

  it("其它键不处理", () => {
    expect(navigatorKeyWidth(260, "ArrowUp", float)).toBeNull();
    expect(navigatorKeyWidth(260, "Enter", float)).toBeNull();
  });
});
