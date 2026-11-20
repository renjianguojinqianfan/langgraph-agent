import { describe, expect, it } from "vitest";
import {
  navigatorPaneFor,
  updateNavigatorPane,
  type NavigatorPanes,
} from "./navigatorState";

describe("navigatorPaneFor", () => {
  it("未知任务：默认关闭、没有记忆宽度", () => {
    expect(navigatorPaneFor({}, "t1")).toEqual({ open: false, width: null });
  });
});

describe("updateNavigatorPane", () => {
  it("按 task 隔离：一个任务的开合与宽度不影响另一个", () => {
    let panes: NavigatorPanes = {};
    panes = updateNavigatorPane(panes, "t1", { open: true, width: 280 });
    panes = updateNavigatorPane(panes, "t2", { open: false, width: 200 });

    expect(navigatorPaneFor(panes, "t1")).toEqual({ open: true, width: 280 });
    expect(navigatorPaneFor(panes, "t2")).toEqual({ open: false, width: 200 });
  });

  it("局部 patch 保留同一任务的其它字段", () => {
    let panes = updateNavigatorPane({}, "t1", { open: true, width: 240 });
    panes = updateNavigatorPane(panes, "t1", { open: false });
    expect(navigatorPaneFor(panes, "t1")).toEqual({ open: false, width: 240 });

    panes = updateNavigatorPane(panes, "t1", { width: 180 });
    expect(navigatorPaneFor(panes, "t1")).toEqual({ open: false, width: 180 });
  });

  it("不修改传入的 map（纯更新）", () => {
    const before: NavigatorPanes = { t1: { open: false, width: null } };
    const after = updateNavigatorPane(before, "t1", { open: true });
    expect(before.t1).toEqual({ open: false, width: null });
    expect(after.t1).toEqual({ open: true, width: null });
  });
});
