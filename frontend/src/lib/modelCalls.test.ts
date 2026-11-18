import { describe, expect, it } from "vitest";
import type { SSEvent } from "../types";
import { callCompleteness, collectModelCalls, modelCallSummary } from "./modelCalls";

function record(
  overrides: Record<string, unknown> = {}
): SSEvent {
  return {
    type: "llm_call",
    data: {
      call_id: "call-1",
      attempt: 1,
      owner_task_id: "t1",
      source: "main",
      round: 1,
      step: 2,
      model: "m1",
      protocol: "mock",
      started_at: 10,
      ended_at: 11,
      status: "ok",
      request: { messages: [] },
      response: { choices: [] },
      error: null,
      truncated: false,
      usage: null,
      pending: false,
      ...overrides,
    },
  };
}

describe("模型调用记录读取（S8）", () => {
  it("同一 attempt 的占位与最终记录只留最后一条", () => {
    const calls = collectModelCalls([
      record({ status: "aborted", pending: true, response: null }),
      record({ status: "ok", pending: false }),
    ]);
    expect(calls).toHaveLength(1);
    expect(calls[0].records).toHaveLength(1);
    expect(calls[0].latest.status).toBe("ok");
    expect(callCompleteness(calls[0])).toBe("complete");
  });

  it("重连重放同一 attempt 不翻倍、不复活占位", () => {
    const final = record({ status: "error", error: "boom", response: null });
    const placeholder = record({ status: "aborted", pending: true });
    const once = collectModelCalls([placeholder, final]);
    const twice = collectModelCalls([placeholder, final, placeholder, final]);
    expect(twice).toEqual(once);
    expect(twice[0].latest.status).toBe("error");
    expect(twice[0].latest.error).toBe("boom");
  });

  it("SDK 重试的多个 attempt 按次数排序，列表读最后一次", () => {
    const calls = collectModelCalls([
      record({ attempt: 2, started_at: 12, status: "ok" }),
      record({ attempt: 1, started_at: 10, status: "error", error: "HTTP 500" }),
    ]);
    expect(calls[0].records.map((r) => r.attempt)).toEqual([1, 2]);
    expect(calls[0].latest.attempt).toBe(2);
    expect(calls[0].latest.status).toBe("ok");
  });

  it("中断留下的占位显示为未完成", () => {
    const calls = collectModelCalls([
      record({ status: "aborted", pending: true, ended_at: null }),
    ]);
    expect(callCompleteness(calls[0])).toBe("pending");
  });

  it("请求预览被截断时明确标记", () => {
    const calls = collectModelCalls([record({ truncated: true })]);
    expect(callCompleteness(calls[0])).toBe("truncated");
  });

  it("失败记录仍可用于列表，摘要不依赖响应体", () => {
    const calls = collectModelCalls([
      record({ status: "error", response: null, error: "HTTP 429" }),
    ]);
    expect(modelCallSummary(calls[0])).toContain("main");
    expect(modelCallSummary(calls[0])).toContain("error");
  });

  it("畸形或非 llm_call 帧被忽略", () => {
    const calls = collectModelCalls([
      { type: "tool_call", data: { id: "c1" } },
      { type: "llm_call", data: { call_id: "", attempt: 1 } },
      { type: "llm_call", data: { call_id: "x", attempt: 0 } },
    ]);
    expect(calls).toEqual([]);
  });

  it("多个 call 按开始时间排序，归属 task 保留在记录里", () => {
    const calls = collectModelCalls([
      record({ call_id: "b", started_at: 20 }),
      record({ call_id: "a", started_at: 10, owner_task_id: "parent" }),
    ]);
    expect(calls.map((c) => c.call_id)).toEqual(["a", "b"]);
    expect(calls[0].latest.owner_task_id).toBe("parent");
  });
});
