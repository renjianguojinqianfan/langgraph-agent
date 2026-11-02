import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "./authStore";

/** Stub global fetch the way `loginAttempt` sees it (status + json body). */
function stubFetch(impl: () => Promise<{ status: number; json: () => Promise<unknown> }>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(impl)
  );
}

function stubStatus(status: number, body: unknown = {}) {
  stubFetch(async () => ({ status, json: async () => body }));
}

beforeEach(() => {
  useAuthStore.setState({ token: null, authEnabled: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("#256 登录：口令错误 ≠ 传输/服务端失败", () => {
  it("200 + ok + token → ok，令牌入 store", async () => {
    stubStatus(200, { data: { token: "tk", expires_at: "x", ok: true } });

    const outcome = await useAuthStore.getState().login("secret");

    expect(outcome).toBe("ok");
    expect(useAuthStore.getState().token).toBe("tk");
  });

  it("401 → wrong-secret（不写令牌）", async () => {
    stubStatus(401, { detail: "invalid token" });

    const outcome = await useAuthStore.getState().login("nope");

    expect(outcome).toBe("wrong-secret");
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("fetch 抛错（传输失败）→ unreachable", async () => {
    stubFetch(async () => {
      throw new TypeError("Failed to fetch");
    });

    const outcome = await useAuthStore.getState().login("whatever");

    expect(outcome).toBe("unreachable");
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("500 → unreachable，不冤枉口令", async () => {
    stubStatus(500, { detail: "boom" });

    const outcome = await useAuthStore.getState().login("whatever");

    expect(outcome).toBe("unreachable");
  });

  it("200 但信封不 ok → unreachable", async () => {
    stubStatus(200, { data: { ok: false } });

    const outcome = await useAuthStore.getState().login("whatever");

    expect(outcome).toBe("unreachable");
    expect(useAuthStore.getState().token).toBeNull();
  });
});
