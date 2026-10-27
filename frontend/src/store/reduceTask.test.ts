import { beforeEach, describe, expect, it } from "vitest";
import { SSEvent, Task } from "../types";
import { needsStatusRefetch } from "./reduceTask";
import { useTaskStore } from "./taskStore";

function snapshot(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "任务",
    user_input: "做点事",
    workspace: "E:/work",
    model: "",
    status: "RUNNING",
    mode: "build",
    steps: [{ index: 1, thought: "先看一眼", tool_calls: [], status: "running" }],
    plan_version: 0,
    conversation: [{ role: "user", content: "做点事" }],
    final_answer: "",
    error: null,
    created_at: "2026-10-27T00:00:00",
    updated_at: "2026-10-27T00:00:01",
    subtasks: [],
    ...overrides,
  };
}

beforeEach(() => {
  useTaskStore.setState({
    tasks: [],
    currentTaskId: null,
    detail: {},
    eventsByTask: {},
    markers: {},
    historyFilter: "current",
    settingsOpen: false,
    listRevision: 0,
    listError: "",
  });
});

describe("#189 回放不覆盖任务快照", () => {
  it("RUNNING 快照 + 回放 task_created(PENDING) → 仍 RUNNING，标题也不被覆盖", () => {
    useTaskStore.setState({ currentTaskId: "t1", detail: { t1: snapshot() } });

    useTaskStore.getState().applyEvent({
      type: "task_created",
      data: { task_id: "t1", title: "回放里的旧标题", status: "PENDING" },
    });

    const task = useTaskStore.getState().detail["t1"];
    expect(task.status).toBe("RUNNING");
    expect(task.title).toBe("任务");
  });

  it("无快照 + task_created(PENDING) → 写入 PENDING 快照", () => {
    useTaskStore.setState({ currentTaskId: "t2" });

    useTaskStore.getState().applyEvent({
      type: "task_created",
      data: { task_id: "t2", title: "新任务", status: "PENDING" },
    });

    const task = useTaskStore.getState().detail["t2"];
    expect(task.status).toBe("PENDING");
    expect(task.title).toBe("新任务");
  });

  it("列表已有行、detail 无快照时，回放 task_created 不覆写列表行", () => {
    useTaskStore.setState({ tasks: [snapshot()], currentTaskId: "t1" });

    useTaskStore.getState().applyEvent({
      type: "task_created",
      data: { task_id: "t1", title: "回放里的旧标题", status: "PENDING" },
    });

    const state = useTaskStore.getState();
    expect(state.detail["t1"].status).toBe("RUNNING");
    expect(state.detail["t1"].title).toBe("任务");
    expect(state.tasks[0].status).toBe("RUNNING");
  });

  it("重取快照只合并状态字段，不清空 steps / conversation", () => {
    useTaskStore.setState({
      currentTaskId: "t1",
      detail: { t1: snapshot({ status: "PENDING" }) },
    });

    // REST 快照只负责状态字段；steps / conversation 以 SSE 实时数据为准。
    useTaskStore.getState().mergeTaskStatus(
      snapshot({
        status: "RUNNING",
        steps: [],
        conversation: [],
        updated_at: "2026-10-27T00:00:09",
      })
    );

    const task = useTaskStore.getState().detail["t1"];
    expect(task.status).toBe("RUNNING");
    expect(task.updated_at).toBe("2026-10-27T00:00:09");
    expect(task.steps).toHaveLength(1);
    expect(task.conversation).toHaveLength(1);
  });
});

describe("#189 step_start 的状态重取判定", () => {
  it("step_start + 仍是 PENDING → 需要重取", () => {
    expect(
      needsStatusRefetch(snapshot({ status: "PENDING" }), {
        type: "step_start",
        data: { task_id: "t1", index: 1 },
      })
    ).toBe(true);
  });

  it("RUNNING 或非 step_start 事件 → 不重取", () => {
    const stepStart: SSEvent = {
      type: "step_start",
      data: { task_id: "t1", index: 1 },
    };
    expect(needsStatusRefetch(snapshot({ status: "RUNNING" }), stepStart)).toBe(false);
    expect(
      needsStatusRefetch(snapshot({ status: "PENDING" }), {
        type: "tool_call",
        data: { task_id: "t1", tool_call_id: "c1" },
      })
    ).toBe(false);
  });
});

describe("子智能体事件只动 subtasks", () => {
  const seed = () =>
    useTaskStore.setState({
      currentTaskId: "t1",
      detail: { t1: snapshot({ subtasks: [] }) },
    });

  it("subtask_start 插入等待→运行态，摘要与产物清空，steps/conversation 不动", () => {
    seed();
    useTaskStore.getState().applyEvent({
      type: "subtask_start",
      data: { task_id: "t1", subtask_id: "s1", name: "写报告", status: "running" },
    });

    const task = useTaskStore.getState().detail["t1"];
    expect(task.subtasks).toEqual([
      {
        subtask_id: "s1",
        name: "写报告",
        status: "running",
        summary: "",
        artifacts: [],
        error: null,
      },
    ]);
    expect(task.steps).toHaveLength(1);
    expect(task.conversation).toHaveLength(1);
  });

  it("subtask_result 更新状态、摘要与产物", () => {
    seed();
    useTaskStore.getState().applyEvent({
      type: "subtask_start",
      data: { task_id: "t1", subtask_id: "s1", name: "写报告", status: "running" },
    });
    useTaskStore.getState().applyEvent({
      type: "subtask_result",
      data: {
        task_id: "t1",
        subtask_id: "s1",
        name: "写报告",
        status: "completed",
        summary: "三段结论",
        artifacts: ["reports/a.md"],
      },
    });

    const sub = useTaskStore.getState().detail["t1"].subtasks?.[0];
    expect(sub?.status).toBe("completed");
    expect(sub?.summary).toBe("三段结论");
    expect(sub?.artifacts).toEqual(["reports/a.md"]);
  });

  it("subtask_failed 置失败并保留原因，不重建 steps", () => {
    seed();
    useTaskStore.getState().applyEvent({
      type: "subtask_failed",
      data: { task_id: "t1", subtask_id: "s2", name: "抓数据", error: "超时" },
    });

    const task = useTaskStore.getState().detail["t1"];
    expect(task.subtasks?.[0]).toMatchObject({
      subtask_id: "s2",
      status: "failed",
      error: "超时",
    });
    expect(task.steps).toHaveLength(1);
    expect(task.conversation).toHaveLength(1);
  });
});

describe("压缩标记按回放去重", () => {
  it("同一 context_compressed 帧重放两次，只留一条标记", () => {
    useTaskStore.setState({ currentTaskId: "t1", detail: { t1: snapshot() } });
    const ev: SSEvent = {
      type: "context_compressed",
      data: {
        task_id: "t1",
        step_index: 1,
        dropped: 0,
        band_evicted: 2,
        context_tokens: 1000,
        strategy: "summary",
      },
    };

    useTaskStore.getState().applyEvent(ev);
    useTaskStore.getState().applyEvent(ev);

    expect(useTaskStore.getState().markers["t1"]).toHaveLength(1);
  });
});
