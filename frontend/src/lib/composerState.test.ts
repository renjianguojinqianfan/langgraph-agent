import { describe, expect, it } from "vitest";
import type { Task, TaskStatus } from "../types";
import { clearedDraft, composerView } from "./composerState";

function task(status: TaskStatus): Task {
  return {
    id: "t1",
    title: "t1",
    user_input: "",
    workspace: "E:\\w",
    model: "",
    status,
    mode: "build",
    steps: [],
    plan_version: 0,
    conversation: [],
    final_answer: "",
    created_at: "2026-10-28T00:00:00Z",
    updated_at: "2026-10-28T00:00:00Z",
  };
}

const base = {
  task: undefined as Task | undefined,
  currentTaskId: null as string | null,
  detailError: "",
  detailAuthExpired: false,
  text: "",
  busy: false,
  sendError: null as string | null,
};

const present = (over: Partial<typeof base> = {}) =>
  composerView({ ...base, ...over });

describe("composerView (#232 / D-32 state matrix)", () => {
  it("no task, no id: a fresh task can be sent", () => {
    const view = present({ text: "hi" });
    expect(view).toEqual({
      parked: false,
      failed: false,
      running: false,
      loading: false,
      inputDisabled: false,
      canSend: true,
      disabledReason: null,
      placeholder: "用自然语言描述一个任务，例如：联网查资料并写一份报告…",
      bottomNote: null,
    });
  });

  it("selected id without a snapshot is loading, not a new task", () => {
    const view = present({ currentTaskId: "t1", text: "hi" });
    expect(view.loading).toBe(true);
    expect(view.inputDisabled).toBe(true);
    expect(view.canSend).toBe(false);
    expect(view.disabledReason).toBe("正在加载任务…");
    expect(view.placeholder).toBe("…");
  });

  it("detail error: auth-expired vs generic reason, placeholder dots", () => {
    const generic = present({
      currentTaskId: "t1",
      detailError: "任务加载失败",
      text: "hi",
    });
    expect(generic.inputDisabled).toBe(true);
    expect(generic.disabledReason).toBe("任务加载失败；重试或新建任务");
    expect(generic.placeholder).toBe("…");

    const auth = present({
      currentTaskId: "t1",
      detailError: "401",
      detailAuthExpired: true,
    });
    expect(auth.disabledReason).toBe("登录已失效；重新登录后再试");
  });

  it("PENDING is running/stoppable and disabled", () => {
    const view = present({ task: task("PENDING"), currentTaskId: "t1", text: "hi" });
    expect(view.running).toBe(true);
    expect(view.parked).toBe(false);
    expect(view.inputDisabled).toBe(true);
    expect(view.disabledReason).toBe("任务启动中，稍等片刻…");
    expect(view.placeholder).toBe("…");
  });

  it("RUNNING is disabled with its own reason", () => {
    const view = present({ task: task("RUNNING"), currentTaskId: "t1", text: "hi" });
    expect(view.running).toBe(true);
    expect(view.inputDisabled).toBe(true);
    expect(view.disabledReason).toBe("任务运行中；停下或等这一轮结束再发");
    expect(view.placeholder).toBe("…");
  });

  it("INTERRUPTED is parked: input enabled, continue copy", () => {
    const view = present({ task: task("INTERRUPTED"), currentTaskId: "t1", text: "hi" });
    expect(view.parked).toBe(true);
    expect(view.failed).toBe(false);
    expect(view.inputDisabled).toBe(false);
    expect(view.canSend).toBe(true);
    expect(view.disabledReason).toBeNull();
    expect(view.placeholder).toBe("接着说一句，任务从这里继续…");
  });

  it("FAILED is parked and failed, with the error-continue copy", () => {
    const view = present({ task: task("FAILED"), currentTaskId: "t1", text: "hi" });
    expect(view.failed).toBe(true);
    expect(view.parked).toBe(true);
    expect(view.inputDisabled).toBe(false);
    expect(view.placeholder).toBe("任务出错，接着说一句继续…");
  });

  it("canSend also needs non-blank text and no busy request", () => {
    expect(present({ text: "   " }).canSend).toBe(false);
    expect(present({ text: "hi", busy: true }).canSend).toBe(false);
  });

  it("bottomNote prefers the disable reason over the send error", () => {
    const view = present({
      currentTaskId: "t1",
      detailError: "任务加载失败",
      sendError: "发送失败：…",
    });
    expect(view.bottomNote).toBe("任务加载失败；重试或新建任务");
    const sendOnly = present({ sendError: "发送失败：…" });
    expect(sendOnly.bottomNote).toBe("发送失败：…");
  });
});

describe("clearedDraft (#232: only clear the sent draft)", () => {
  it("clears when the current text equals the sent text", () => {
    expect(clearedDraft("hello", "hello")).toBe("");
  });

  it("keeps text typed while the request was in flight (trim semantics)", () => {
    expect(clearedDraft("hello world", "hello")).toBe("hello world");
    expect(clearedDraft("  hello  ", "hello")).toBe("");
  });

  it("difference after trim does not clear", () => {
    expect(clearedDraft("hellos", "hello")).toBe("hellos");
  });
});
