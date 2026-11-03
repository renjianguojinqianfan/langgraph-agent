import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Task } from "../types";
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
    authExpired: false,
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

describe("#256 authExpired（401 全局位）", () => {
  it("setAuthExpired 记录/清除，与 detailError 互不依赖", () => {
    useTaskStore.getState().setAuthExpired(true);
    expect(useTaskStore.getState().authExpired).toBe(true);

    useTaskStore.getState().setAuthExpired(false);
    expect(useTaskStore.getState().authExpired).toBe(false);
  });

  it("401 置位不清 detail（重新登录后回到原任务）", () => {
    useTaskStore.setState({ detail: { t1: task("t1") } });
    useTaskStore.getState().setAuthExpired(true);
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
