import { describe, expect, it } from "vitest";
import { drawerView } from "./drawer";

// #257 AC 的机械面：边界（恰好 860 / 1180）、变宽清遮罩（孤儿 scrim 回归）、
// 互斥、Trace 让位。组件是否消费这些派生值由手测清单兜底（D-24）。
describe("drawerView (D-26)", () => {
  it("860 恰好不窄：open=left 也不出遮罩，并标记 closeOnWiden", () => {
    const view = drawerView({
      leftNarrow: false,
      rightNarrow: false,
      open: "left",
      traceOpen: false,
    });
    expect(view.justify).toBeNull();
    expect(view.overlayVisible).toBe(false);
    expect(view.backgroundInert).toBe(false);
    expect(view.closeOnWiden).toBe(true);
    expect(view.closeForTrace).toBe(false);
  });

  it("1180 恰好不窄：open=right 同理（不是 <=1180）", () => {
    const view = drawerView({
      leftNarrow: false,
      rightNarrow: false,
      open: "right",
      traceOpen: false,
    });
    expect(view.justify).toBeNull();
    expect(view.overlayVisible).toBe(false);
    expect(view.closeOnWiden).toBe(true);
  });

  it("窄 + 侧已开：遮罩可见、背景 inert、justify 指向该侧", () => {
    const left = drawerView({
      leftNarrow: true,
      rightNarrow: true,
      open: "left",
      traceOpen: false,
    });
    expect(left).toEqual({
      overlayVisible: true,
      backgroundInert: true,
      closeOnWiden: false,
      closeForTrace: false,
      justify: "left",
    });

    const right = drawerView({
      leftNarrow: true,
      rightNarrow: true,
      open: "right",
      traceOpen: false,
    });
    expect(right.justify).toBe("right");
    expect(right.overlayVisible).toBe(true);
  });

  it("变宽（narrow→false）：遮罩必然消失且要求关抽屉（孤儿遮罩回归）", () => {
    const view = drawerView({
      leftNarrow: false,
      rightNarrow: false,
      open: "left",
      traceOpen: false,
    });
    expect(view.overlayVisible).toBe(false);
    expect(view.closeOnWiden).toBe(true);
  });

  it("Trace 打开 + 侧已开：要求关抽屉，遮罩不覆盖 Trace", () => {
    const view = drawerView({
      leftNarrow: true,
      rightNarrow: true,
      open: "right",
      traceOpen: true,
    });
    expect(view.closeForTrace).toBe(true);
    expect(view.overlayVisible).toBe(false);
    expect(view.backgroundInert).toBe(false);
    expect(view.justify).toBeNull();
  });

  it("open=null：无遮罩、无关闭信号", () => {
    const view = drawerView({
      leftNarrow: true,
      rightNarrow: true,
      open: null,
      traceOpen: false,
    });
    expect(view).toEqual({
      overlayVisible: false,
      backgroundInert: false,
      closeOnWiden: false,
      closeForTrace: false,
      justify: null,
    });
  });

  it("互斥：两侧都窄时只有 open 侧可 justify（单值入口）", () => {
    expect(
      drawerView({ leftNarrow: true, rightNarrow: true, open: "right", traceOpen: false })
        .justify
    ).toBe("right");
    expect(
      drawerView({ leftNarrow: true, rightNarrow: true, open: "left", traceOpen: false })
        .justify
    ).toBe("left");
  });
});