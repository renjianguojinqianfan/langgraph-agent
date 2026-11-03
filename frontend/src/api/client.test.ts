import { afterEach, describe, expect, it, vi } from "vitest";
import { getTaskSnapshot, listTasksSnapshot } from "./client";
import { detailLoadFailure, detailLoadState, listLoadOutcome } from "../lib/failureStates";

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

/**
 * #256 复审回归的硬证据：**2xx + 不合规信封**。`VITE_API_BASE` 指错、SPA
 * fallback 回 200 HTML、网关回 200 错误体时，详情读取曾经走
 * `detailLoadFailure("ready")` → 空串 → 删掉 detailError → 永久「加载中…」。
 */
describe("getTaskSnapshot (#256 复审：2xx 空信封不走死路)", () => {
  it("200 + 非信封 body → bad-response 错误面，不是永久加载", async () => {
    stubFetch(async () => ({
      status: 200,
      // SPA fallback / gateway error page: 200, but not the API envelope.
      json: async () => "<!doctype html><title>proxy error</title>",
    }));

    const r = await getTaskSnapshot("t1");
    expect(r.status).toBe(200);
    expect(r.data).toBeNull();

    // TaskView 的判定链：2xx + 无数据 = 失败，文案非空 → 错误面 + 重试可达。
    const state = detailLoadState(r.status, r.data !== null);
    expect(state).toBe("bad-response");
    const copy = detailLoadFailure(state, r.status);
    expect(copy).not.toBe("");
    expect(copy).toContain("响应异常");
  });

  it("200 + 信封 → ready，数据原样带回", async () => {
    const data = { id: "t1" };
    stubFetch(async () => ({ status: 200, json: async () => ({ data }) }));

    const r = await getTaskSnapshot("t1");
    expect(r.status).toBe(200);
    expect(r.data).toEqual(data);
    expect(detailLoadState(r.status, r.data !== null)).toBe("ready");
  });

  it("404 → missing（任务不存在，不是新任务）", async () => {
    stubFetch(async () => ({
      status: 404,
      json: async () => ({ detail: "task not found" }),
    }));

    const r = await getTaskSnapshot("gone");
    expect(r.status).toBe(404);
    expect(detailLoadState(r.status, r.data !== null)).toBe("missing");
    expect(detailLoadFailure("missing", r.status)).toContain("不存在");
  });
});
