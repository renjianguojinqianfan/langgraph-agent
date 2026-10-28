import { beforeEach, describe, expect, it } from "vitest";
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
});
