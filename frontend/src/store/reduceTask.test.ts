import { beforeEach, describe, expect, it } from "vitest";
import { SSEvent, Task } from "../types";
import { applyToTask, mergeSnapshotUnlessStale, needsStatusRefetch } from "./reduceTask";
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

describe("#265 step_start 的轮次归属", () => {
  it("round 落到新建步骤上", () => {
    const next = applyToTask(snapshot({ steps: [] }), "t1", {
      type: "step_start",
      data: { task_id: "t1", index: 1, round: 2, thought: "做点事" },
    });
    expect(next.steps[0]).toMatchObject({ index: 1, round: 2, thought: "做点事" });
  });

  it("旧事件没有 round 时不伪造轮次", () => {
    const next = applyToTask(snapshot({ steps: [] }), "t1", {
      type: "step_start",
      data: { task_id: "t1", index: 1 },
    });
    expect(next.steps[0].round).toBeUndefined();
  });

  it("已有步骤收到带 round 的 step_start 时补上轮次", () => {
    const next = applyToTask(snapshot(), "t1", {
      type: "step_start",
      data: { task_id: "t1", index: 1, round: 1 },
    });
    expect(next.steps[0].round).toBe(1);
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

describe("#256 迟到快照不清 SSE 已渲染步骤", () => {
  const live = () =>
    snapshot({
      status: "RUNNING",
      steps: [
        { index: 1, thought: "第一步", tool_calls: [], status: "done" },
        { index: 2, thought: "第二步", tool_calls: [], status: "running" },
      ],
      conversation: [
        { role: "user", content: "做点事" },
        { role: "assistant", content: "好的" },
      ],
      updated_at: "2026-10-27T00:00:05",
    });

  it("快照步骤更少（请求早于流）→ 保留实时 steps / conversation / 状态", () => {
    const stale = snapshot({
      status: "PENDING",
      steps: [{ index: 1, thought: "第一步", tool_calls: [], status: "done" }],
      conversation: [{ role: "user", content: "做点事" }],
      updated_at: "2026-10-27T00:00:01",
    });

    const merged = mergeSnapshotUnlessStale(live(), stale);

    expect(merged.steps).toHaveLength(2);
    expect(merged.conversation).toHaveLength(2);
    // A stale snapshot's status is stale too (#189): live RUNNING wins over PENDING.
    expect(merged.status).toBe("RUNNING");
    // The snapshot stays authoritative for the fields the stream never owns.
    expect(merged.updated_at).toBe("2026-10-27T00:00:01");
    expect(merged.title).toBe(live().title);
  });

  it("AC4：快照更旧时保留 plan_version（迟到旧快照不把版本退回）", () => {
    // A save synced v5 onto the live task; a snapshot requested before that
    // save still says v1 — walking the version back would make the next save
    // start from the wrong one (评审 AC4)。
    const stale = snapshot({
      status: "PENDING",
      plan_version: 1,
      steps: [{ index: 1, thought: "第一步", tool_calls: [], status: "done" }],
    });

    const merged = mergeSnapshotUnlessStale({ ...live(), plan_version: 5 }, stale);

    expect(merged.plan_version).toBe(5);
  });

  it("AC4：步数相等但快照版本更旧（跨保存 in-flight GET）→ 取 max 不退回", () => {
    // Same step count, older version: the old tie-break returned the snapshot
    // wholesale and walked the saved version back (复审 AC4 相等步数分支).
    const equal = snapshot({
      status: "PENDING",
      plan_version: 1,
      steps: [
        { index: 1, thought: "第一步", tool_calls: [], status: "done" },
        { index: 2, thought: "第二步", tool_calls: [], status: "running" },
      ],
    });

    const merged = mergeSnapshotUnlessStale({ ...live(), plan_version: 5 }, equal);

    expect(merged.plan_version).toBe(5);
    // Equal steps still means the snapshot's own fields win (REST 权威).
    expect(merged.status).toBe("PENDING");
  });

  it("AC4：快照版本更新（服务器领先）→ 取快照的版本", () => {
    const ahead = snapshot({
      status: "INTERRUPTED",
      plan_version: 7,
      steps: [
        { index: 1, thought: "第一步", tool_calls: [], status: "done" },
        { index: 2, thought: "第二步", tool_calls: [], status: "running" },
      ],
    });

    const merged = mergeSnapshotUnlessStale({ ...live(), plan_version: 5 }, ahead);

    expect(merged.plan_version).toBe(7);
  });

  it("快照步骤不少于实时（新鲜）→ 快照整体胜出", () => {
    const fresh = snapshot({
      status: "INTERRUPTED",
      steps: [
        { index: 1, thought: "第一步", tool_calls: [], status: "done" },
        { index: 2, thought: "第二步", tool_calls: [], status: "done" },
        { index: 3, thought: "第三步", tool_calls: [], status: "done" },
      ],
      conversation: [
        { role: "user", content: "做点事" },
        { role: "assistant", content: "做完了" },
      ],
      updated_at: "2026-10-27T00:00:09",
    });

    const merged = mergeSnapshotUnlessStale(live(), fresh);

    // Field-for-field the snapshot wins (a spread copy now, not identity —
    // the plan version goes through max()).
    expect(merged).toEqual(fresh);
    expect(merged.steps).toHaveLength(3);
    expect(merged.status).toBe("INTERRUPTED");
  });

  it("步骤数相等 → 快照胜出（REST 权威，含同数的内容差异）", () => {
    const tie = snapshot({
      status: "FAILED",
      steps: [
        { index: 1, thought: "第一步", tool_calls: [], status: "done" },
        { index: 2, thought: "第二步（带结果）", tool_calls: [], status: "failed" },
      ],
      error: "炸了",
    });

    const merged = mergeSnapshotUnlessStale(live(), tie);

    expect(merged.status).toBe("FAILED");
    expect(merged.error).toBe("炸了");
    expect(merged.steps[1].thought).toBe("第二步（带结果）");
  });

  it("store 层：SSE 渲染两步后迟到的 getTask 快照（一步）不清步骤", () => {
    useTaskStore.setState({ currentTaskId: "t1", detail: { t1: live() } });

    // The snapshot requested on selection resolves after the stream caught up.
    useTaskStore.getState().upsertTask(
      snapshot({
        status: "PENDING",
        steps: [{ index: 1, thought: "第一步", tool_calls: [], status: "done" }],
        conversation: [{ role: "user", content: "做点事" }],
        updated_at: "2026-10-27T00:00:01",
      })
    );

    const task = useTaskStore.getState().detail["t1"];
    expect(task.steps).toHaveLength(2);
    expect(task.steps[1].thought).toBe("第二步");
    expect(task.status).toBe("RUNNING");
  });

  it("AC4 接线：保存同步 v5 后，迟到旧快照（v1、步骤更少）不退回版本", () => {
    // The exact #256-AC4 wiring bug: save → setPlanVersion(5) → a snapshot
    // requested before the save lands with plan_version 1.
    useTaskStore.setState({
      currentTaskId: "t1",
      detail: { t1: { ...live(), plan_version: 5 } },
    });
    useTaskStore.getState().upsertTask(
      snapshot({
        status: "PENDING",
        plan_version: 1,
        steps: [{ index: 1, thought: "第一步", tool_calls: [], status: "done" }],
      })
    );

    const task = useTaskStore.getState().detail["t1"];
    expect(task.plan_version).toBe(5);
    // PlanAnchor reads the task's version, so it stays on v5.
    expect(task.steps).toHaveLength(2);
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

describe("D-38/D-40: final_answer reasoning 落到步骤", () => {
  it("带 reasoning 的事件把思考挂到刚产出回答的最后一步", () => {
    const prev = {
      ...snapshot({
        steps: [
          { index: 1, thought: "先看", tool_calls: [], status: "done" },
          { index: 2, thought: "答", tool_calls: [], status: "done" },
        ],
      }),
      final_answer: "",
    };
    const next = applyToTask(prev, "t1", {
      type: "final_answer",
      data: { answer: "最终回答", reasoning: "先读文件，再总结" },
    });
    expect(next.final_answer).toBe("最终回答");
    expect(next.steps[0].reasoning).toBeUndefined();
    expect(next.steps[1].reasoning).toBe("先读文件，再总结");
  });

  it("无 reasoning 的事件不给任何步骤加空块", () => {
    const next = applyToTask(snapshot(), "t1", {
      type: "final_answer",
      data: { answer: "普通回答" },
    });
    expect(next.final_answer).toBe("普通回答");
    expect(next.steps.every((step) => step.reasoning === undefined)).toBe(true);
  });

  it("回放同一事件幂等（重连不产生重复思考块）", () => {
    const event = {
      type: "final_answer" as const,
      data: { answer: "答", reasoning: "想一想" },
    };
    const once = applyToTask(snapshot(), "t1", event);
    const twice = applyToTask(once, "t1", event);
    expect(twice.steps[twice.steps.length - 1].reasoning).toBe("想一想");
    expect(twice.steps.length).toBe(once.steps.length);
  });
});
