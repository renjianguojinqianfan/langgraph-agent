import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Task } from "../types";
import { historyListState, listBannerVisible } from "../lib/failureStates";
import { useTaskStore } from "./taskStore";

function task(id: string, workspace = "E:\\a"): Task {
  return {
    id,
    title: id,
    user_input: "",
    workspace,
    model: "",
    status: "INTERRUPTED",
    mode: "build",
    steps: [],
    plan_version: 0,
    conversation: [],
    final_answer: "",
    created_at: "2026-10-28T00:00:00Z",
    updated_at: "2026-10-28T00:00:00Z",
  };
}

beforeEach(() => {
  useTaskStore.setState({
    tasks: [],
    currentTaskId: null,
    detail: {},
    eventsByTask: {},
    markers: {},
    listRevision: 0,
    listError: "",
    listLoading: false,
    detailError: {},
    detailRevision: 0,
    authExpired: { list: false, detail: false, plan: false },
    historyFilter: "current",
  });
});

describe("removeTask (#241)", () => {
  it("drops the row and all per-task caches, clears the selection, bumps the list", () => {
    useTaskStore.setState({
      tasks: [task("t1"), task("t2")],
      currentTaskId: "t1",
      detail: { t1: task("t1"), t2: task("t2") },
      eventsByTask: { t1: [{ type: "heartbeat", data: {} }], t2: [] },
      markers: { t1: [{ type: "context_compressed", data: {} as never }], t2: [] },
      listRevision: 2,
    });

    useTaskStore.getState().removeTask("t1");

    const state = useTaskStore.getState();
    expect(state.tasks.map((row) => row.id)).toEqual(["t2"]);
    expect(state.detail.t1).toBeUndefined();
    expect(state.eventsByTask.t1).toBeUndefined();
    expect(state.markers.t1).toBeUndefined();
    expect(state.currentTaskId).toBeNull();
    expect(state.listRevision).toBe(3);
  });

  it("keeps an unrelated selection", () => {
    useTaskStore.setState({
      tasks: [task("t1"), task("t2")],
      currentTaskId: "t2",
      detail: { t2: task("t2") },
    });

    useTaskStore.getState().removeTask("t1");

    expect(useTaskStore.getState().currentTaskId).toBe("t2");
  });
});

describe("removeWorkspaceTasks (#241)", () => {
  it("only drops tasks of the named workspace and keeps the other directory", () => {
    useTaskStore.setState({
      tasks: [task("a1", "E:\\a"), task("a2", "E:\\a"), task("b1", "E:\\b")],
      currentTaskId: "a1",
      detail: { a1: task("a1", "E:\\a"), b1: task("b1", "E:\\b") },
    });

    useTaskStore.getState().removeWorkspaceTasks("E:\\a");

    const state = useTaskStore.getState();
    expect(state.tasks.map((row) => row.id)).toEqual(["b1"]);
    expect(state.detail.a1).toBeUndefined();
    expect(state.detail.b1).toBeDefined();
    expect(state.currentTaskId).toBeNull();
  });

  it("clears a selected task that already slid out of the visible list", () => {
    useTaskStore.setState({
      // The 50-row list no longer contains the selected task; only its detail
      // snapshot remembers which directory it belongs to.
      tasks: [task("b1", "E:\\b")],
      currentTaskId: "old",
      detail: { old: task("old", "E:\\a") },
      eventsByTask: { old: [] },
      markers: { old: [] },
    });

    useTaskStore.getState().removeWorkspaceTasks("E:\\a");

    const state = useTaskStore.getState();
    expect(state.detail.old).toBeUndefined();
    expect(state.eventsByTask.old).toBeUndefined();
    expect(state.markers.old).toBeUndefined();
    expect(state.currentTaskId).toBeNull();
    expect(state.tasks.map((row) => row.id)).toEqual(["b1"]);
  });

  it("#256 评审：dropTasks 连 detailError 一起清（per-task cache 不留残留）", () => {
    useTaskStore.setState({
      tasks: [task("a1", "E:\\a"), task("b1", "E:\\b")],
      detailError: { a1: "任务不存在或已被删除。" },
    });

    useTaskStore.getState().removeTask("a1");
    expect(useTaskStore.getState().detailError.a1).toBeUndefined();

    useTaskStore.setState({
      tasks: [task("a2", "E:\\a")],
      detailError: { a2: "无法连接后台；确认服务在运行后再试。" },
    });
    useTaskStore.getState().removeWorkspaceTasks("E:\\a");
    expect(useTaskStore.getState().detailError.a2).toBeUndefined();
  });
});

describe("#256 列表失败态", () => {
  it("setListError / setListLoading 是两块独立状态（失败≠加载中≠空）", () => {
    useTaskStore.getState().setListLoading(true);
    expect(useTaskStore.getState().listLoading).toBe(true);
    expect(useTaskStore.getState().listError).toBe("");

    useTaskStore.getState().setListLoading(false);
    useTaskStore.getState().setListError("无法连接后台；确认服务在运行后再试。");
    const state = useTaskStore.getState();
    expect(state.listLoading).toBe(false);
    expect(state.listError).not.toBe("");
  });

  it("retryList 只推 listRevision，不伪造行、不清错误", () => {
    useTaskStore.setState({ listRevision: 4, listError: "无法连接后台", tasks: [] });

    useTaskStore.getState().retryList();

    const state = useTaskStore.getState();
    expect(state.listRevision).toBe(5);
    expect(state.tasks).toEqual([]);
    // The error stays until the retry answers; clearing it here would show
    // the empty copy while the request is in flight.
    expect(state.listError).not.toBe("");
  });
});

describe("#256 选中任务快照失败", () => {
  it("setDetailError 按任务 id 记/清，互不串味", () => {
    useTaskStore.getState().setDetailError("t1", "任务不存在或已被删除。");

    useTaskStore.getState().setDetailError("t2", "无法连接后台；确认服务在运行后再试。");
    expect(useTaskStore.getState().detailError).toEqual({
      t1: "任务不存在或已被删除。",
      t2: "无法连接后台；确认服务在运行后再试。",
    });

    useTaskStore.getState().setDetailError("t1", "");
    expect(useTaskStore.getState().detailError.t1).toBeUndefined();
    expect(useTaskStore.getState().detailError.t2).toBeDefined();
  });

  it("retryDetail 推 detailRevision（同一行重点也能重取），不清既有错误", () => {
    useTaskStore.setState({ detailRevision: 1, detailError: { t1: "炸了" } });

    useTaskStore.getState().retryDetail();

    const state = useTaskStore.getState();
    expect(state.detailRevision).toBe(2);
    // The error stays visible until the retry answers.
    expect(state.detailError.t1).toBe("炸了");
  });

  it("#256 评审 AC2：detail 已 seed + GET 失败 → 错误态与 detail 并存（错误面不被遮住）", () => {
    // SSE replay / a list row seeds the detail first; the GET then fails.
    // TaskPanel must still be able to render the error face (and its
    // retry / new-task entries) next to the partial task.
    useTaskStore.setState({
      currentTaskId: "t1",
      detail: { t1: task("t1") },
    });

    useTaskStore.getState().setDetailError("t1", "任务不存在或已被删除。");

    const state = useTaskStore.getState();
    expect(state.detail.t1).toBeDefined();
    expect(state.detailError.t1).toBe("任务不存在或已被删除。");
    // The entries the composer copy points at stay reachable: the retry
    // revision still bumps and the error persists until the retry answers.
    useTaskStore.getState().retryDetail();
    expect(useTaskStore.getState().detailError.t1).toBe("任务不存在或已被删除。");
  });
});

describe("#256 authExpired（按来源分存）", () => {
  it("setAuthExpired 按来源记录/清除，互不覆写", () => {
    useTaskStore.getState().setAuthExpired("list", true);
    expect(useTaskStore.getState().authExpired).toEqual({
      list: true,
      detail: false,
      plan: false,
    });

    useTaskStore.getState().setAuthExpired("detail", true);
    useTaskStore.getState().setAuthExpired("list", false);
    expect(useTaskStore.getState().authExpired).toEqual({
      list: false,
      detail: true,
      plan: false,
    });
  });

  it("值不变时不动 store（不触发无谓重渲染）", () => {
    const before = useTaskStore.getState().authExpired;
    useTaskStore.getState().setAuthExpired("list", false);
    expect(useTaskStore.getState().authExpired).toBe(before);
  });

  it("复审 LOW：list 401 + detail 传输失败并发 → 两来源各说各话", () => {
    // The single global flag let one face's 401 relabel another face's
    // transport failure ("重新登录" copy over a "重试" button). Per-source
    // flags keep each face's copy and button from the same answer.
    useTaskStore.getState().setAuthExpired("list", true);
    useTaskStore.getState().setAuthExpired("detail", false);

    const state = useTaskStore.getState().authExpired;
    expect(state.list).toBe(true);
    expect(state.detail).toBe(false);
  });

  it("401 置位不清 detail（重新登录后回到原任务）", () => {
    useTaskStore.setState({ detail: { t1: task("t1") } });
    useTaskStore.getState().setAuthExpired("detail", true);
    expect(useTaskStore.getState().detail.t1).toBeDefined();
  });
});

describe("#256 PlanAnchor 版本同步", () => {
  it("setPlanVersion 同步 detail 与列表行的 plan_version", () => {
    useTaskStore.setState({
      tasks: [task("t1"), task("t2")],
      detail: { t1: task("t1"), t2: task("t2") },
    });

    useTaskStore.getState().setPlanVersion("t1", 3);

    const state = useTaskStore.getState();
    expect(state.detail.t1.plan_version).toBe(3);
    expect(state.tasks[0].plan_version).toBe(3);
    expect(state.detail.t2.plan_version).toBe(0);
  });

  it("版本没变时不动 store（不触发计划重取回路）", () => {
    useTaskStore.setState({ detail: { t1: task("t1") } });
    const before = useTaskStore.getState().detail;

    useTaskStore.getState().setPlanVersion("t1", 0);

    expect(useTaskStore.getState().detail).toBe(before);
  });
});

describe("#256 AC7 首帧不闪空态", () => {
  it("listLoading 初值 true——首个列表应答到达前显示加载态", async () => {
    // A fresh module instance: the effect in TaskView sets the flag one frame
    // after mount, so a false initial value flashes the empty copy first.
    vi.resetModules();
    const fresh = await import("./taskStore");
    expect(fresh.useTaskStore.getState().listLoading).toBe(true);
    vi.resetModules();
    await import("./taskStore");
  });
});

// D-24 接线断言模板（#282）：store action 写入 → 既有纯函数给出组合判定。
// 边界：#282 S1 的完整缺口是「判定正确但组件没消费」；本测试只钉前半段
// （store 状态 → 判定函数）——组件是否消费该判定不在覆盖内，由手测兜底。
// 纯函数单测已覆盖判定本身（lib/failureStates.test.ts）；新状态面孔照抄这个形状即可。
describe("D-24 接线断言模板（#282）", () => {
  const listFace = () => {
    const state = useTaskStore.getState();
    return historyListState({
      error: state.listError,
      loading: state.listLoading,
      rows: state.tasks.length,
      visible: state.tasks.length,
    });
  };

  it("列表读失败 + 零行 → 失败脸；清空 error 后回到空态", () => {
    useTaskStore.getState().setTasks([]);
    useTaskStore.getState().setListLoading(false);
    useTaskStore.getState().setListError("任务列表读取失败（HTTP 500）。");

    expect(listFace()).toBe("error");
    // 失败脸已经承担错误说明，横幅不再重复。
    expect(listBannerVisible(useTaskStore.getState().listError, listFace())).toBe(false);

    useTaskStore.getState().setListError("");
    expect(listFace()).toBe("empty");
  });

  it("有行时 errored refetch 不改写 rows——横幅可见但不落空/失败脸", () => {
    useTaskStore.getState().setTasks([task("t1")]);
    useTaskStore.getState().setListError("任务列表读取失败（HTTP 500）。");

    expect(listFace()).toBe("rows");
    expect(listBannerVisible(useTaskStore.getState().listError, listFace())).toBe(true);
  });
});
