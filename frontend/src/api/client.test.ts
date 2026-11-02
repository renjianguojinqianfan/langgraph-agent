import { afterEach, describe, expect, it, vi } from "vitest";
import { listTasksSnapshot } from "./client";
import { listLoadOutcome } from "../lib/failureStates";

/** Stub global fetch the way the status-preserving readers see it. */
function stubFetch(impl: () => Promise<{ status: number; json: () => Promise<unknown> }>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(impl)
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * #256 评审 S1 的硬证据：票面点名的复现场景——后端返回 **JSON body 的非 2xx**。
 * `req()` 不查 res.ok，这种响应曾经让列表静默落「暂无任务」。
 */
describe("listTasksSnapshot (#256 评审 S1)", () => {
  it("500 + JSON detail → status=500、data=null → 错误面孔而非空态", async () => {
    stubFetch(async () => ({
      status: 500,
      json: async () => ({ detail: "persistence blew up" }),
    }));

    const r = await listTasksSnapshot(50, "current");
    expect(r.status).toBe(500);
    expect(r.data).toBeNull();
    expect(r.detail).toBe("persistence blew up");

    const outcome = listLoadOutcome(r.status, r.data?.tasks ?? null);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.error).toContain("HTTP 500");
  });

  it("401 + JSON detail → 登录已失效面孔", async () => {
    stubFetch(async () => ({
      status: 401,
      json: async () => ({ detail: "invalid token" }),
    }));

    const r = await listTasksSnapshot(50, "current");

    const outcome = listLoadOutcome(r.status, r.data?.tasks ?? null);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.error).toContain("登录已失效");
  });

  it("200 + tasks → 行态数据原样带回", async () => {
    const tasks = [{ id: "t1" }];
    stubFetch(async () => ({ status: 200, json: async () => ({ data: { tasks } }) }));

    const r = await listTasksSnapshot(50, "current");
    expect(r.status).toBe(200);
    expect(listLoadOutcome(r.status, r.data?.tasks ?? null)).toEqual({
      ok: true,
      tasks,
    });
  });

  it("fetch 抛错（传输失败）→ status 0", async () => {
    stubFetch(async () => {
      throw new TypeError("Failed to fetch");
    });

    const r = await listTasksSnapshot(50, "current");
    expect(r.status).toBe(0);
    expect(r.data).toBeNull();
  });
});
