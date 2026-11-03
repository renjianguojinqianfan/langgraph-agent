import { describe, expect, it } from "vitest";
import {
  detailLoadFailure,
  detailLoadState,
  historyListState,
  listLoadFailure,
  listLoadOutcome,
  loginOutcome,
  planLoadFailure,
  planLoadState,
} from "./failureStates";

describe("historyListState (#256: 失败不伪装空列表)", () => {
  it("加载失败且无行 → 错误态（不是「暂无任务」）", () => {
    expect(
      historyListState({ error: "无法连接后台", loading: false, rows: 0, visible: 0 })
    ).toBe("error");
  });

  it("加载中且无行 → 加载态（与空态可区分）", () => {
    expect(
      historyListState({ error: "", loading: true, rows: 0, visible: 0 })
    ).toBe("loading");
  });

  it("既没在加载也没失败且无行 → 空态", () => {
    expect(
      historyListState({ error: "", loading: false, rows: 0, visible: 0 })
    ).toBe("empty");
  });

  it("有行 → 行态；搜索过滤空也算空态而不是错误", () => {
    expect(
      historyListState({ error: "", loading: false, rows: 3, visible: 3 })
    ).toBe("rows");
    expect(
      historyListState({ error: "", loading: false, rows: 3, visible: 0 })
    ).toBe("empty");
  });

  it("刷新失败但上次成功有行 → 保留行态（陈旧但真实）", () => {
    expect(
      historyListState({ error: "无法连接后台", loading: false, rows: 2, visible: 2 })
    ).toBe("rows");
  });
});

describe("detailLoadState (#256: 选中失败不伪装新任务)", () => {
  it("200 + 有数据 → ready", () => {
    expect(detailLoadState(200, true)).toBe("ready");
  });

  it("404 → missing", () => {
    expect(detailLoadState(404, false)).toBe("missing");
  });

  it("401 → auth（token 失效，不是服务端的错）", () => {
    expect(detailLoadState(401, false)).toBe("auth");
  });

  it("传输失败（0）→ unreachable；5xx → server（评审：500 时服务在运行，不能叫连不上）", () => {
    expect(detailLoadState(0, false)).toBe("unreachable");
    expect(detailLoadState(500, false)).toBe("server");
    expect(detailLoadState(502, false)).toBe("server");
  });

  it("复审回归：200 + 信封不可用 → bad-response（不再是 ready+空文案死路）", () => {
    // VITE_API_BASE 指错 / SPA fallback 回 200 HTML / 网关回 200 错误体：
    // 2xx 但没有快照。旧链路 detailLoadFailure("ready") 返空串 → 删掉
    // detailError → 永久「加载中…」且无重试入口。
    expect(detailLoadState(200, false)).toBe("bad-response");
    expect(detailLoadFailure("bad-response", 200)).not.toBe("");
    expect(detailLoadFailure("bad-response", 200)).toContain("响应异常");
  });

  it("每种失败状态的文案都非空（空串会删掉 detailError，就是那条死路）", () => {
    const failures = [
      "missing",
      "auth",
      "unreachable",
      "server",
      "bad-response",
    ] as const;
    for (const state of failures) {
      expect(detailLoadFailure(state, 200)).not.toBe("");
    }
  });

  it("四种失败各有归因文案，都不说「新任务」", () => {
    expect(detailLoadFailure("missing")).toContain("不存在");
    expect(detailLoadFailure("auth")).toContain("重新登录");
    expect(detailLoadFailure("unreachable")).toContain("无法连接后台");
    expect(detailLoadFailure("server", 500)).toContain("HTTP 500");
    // 500 的文案不得含「无法连接后台」——服务端明明在运行（评审硬项）。
    expect(detailLoadFailure("server", 500)).not.toContain("无法连接后台");
  });
});

describe("planLoadState (#256: 计划读败不伪装空计划)", () => {
  it("200 → ready（空文件也是 200，不是失败）", () => {
    expect(planLoadState(200)).toBe("ready");
  });

  it("404 / 409 / 传输失败 → error", () => {
    expect(planLoadState(404)).toBe("error");
    expect(planLoadState(409)).toBe("error");
    expect(planLoadState(0)).toBe("error");
    expect(planLoadState(500)).toBe("error");
  });

  it("失败文案分行：任务没了 / 文件读不出 / 连不上", () => {
    expect(planLoadFailure(404)).toContain("不存在");
    expect(planLoadFailure(409, "plan file is not valid UTF-8")).toContain(
      "plan file is not valid UTF-8"
    );
    expect(planLoadFailure(0)).toContain("无法连接后台");
    expect(planLoadFailure(503)).toContain("HTTP 503");
  });

  it("200 + 不合规信封 → 不产出「HTTP 200」矛盾文案（评审项）", () => {
    expect(planLoadFailure(200)).not.toContain("HTTP 200");
    expect(planLoadFailure(200)).toContain("响应异常");
  });
});

describe("listLoadOutcome (#256 评审 S1: JSON-bodied 500 不落空态)", () => {
  const rows = [
    {
      id: "t1",
      title: "任务",
      user_input: "做点事",
      workspace: "E:/work",
      model: "",
      status: "INTERRUPTED" as const,
      mode: "build" as const,
      steps: [],
      plan_version: 0,
      conversation: [],
      final_answer: "",
      created_at: "",
      updated_at: "",
    },
  ];

  it("500（JSON body 的 detail）→ 错误面孔，不是空态", () => {
    const outcome = listLoadOutcome(500, null);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.error).toContain("HTTP 500");
  });

  it("401（token 失效）→ 登录已失效的专属文案 + authExpired 标记", () => {
    const outcome = listLoadOutcome(401, null);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.error).toContain("登录已失效");
      // AuthGuard 只查 token 存在：刷新留不住新登录，下一步必须是重新登录入口。
      expect(outcome.error).not.toContain("刷新页面");
      expect(outcome.authExpired).toBe(true);
    }
  });

  it("非 401 失败 → authExpired=false", () => {
    for (const status of [0, 500, 503]) {
      const outcome = listLoadOutcome(status, null);
      if (!outcome.ok) expect(outcome.authExpired).toBe(false);
    }
  });

  it("传输失败（0）→ 无法连接后台", () => {
    const outcome = listLoadOutcome(0, null);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.error).toContain("无法连接后台");
  });

  it("200 但信封没有 tasks（响应异常）→ 错误面孔", () => {
    const outcome = listLoadOutcome(200, null);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.error).toContain("响应异常");
  });

  it("200 + tasks → 成功；真的空列表也是成功（空态有理）", () => {
    expect(listLoadOutcome(200, rows)).toEqual({ ok: true, tasks: rows });
    expect(listLoadOutcome(200, [])).toEqual({ ok: true, tasks: [] });
  });

  it("listLoadFailure 的 5xx 文案带状态码", () => {
    expect(listLoadFailure(503)).toContain("HTTP 503");
  });
});

describe("loginOutcome (#256: 口令错误 ≠ 传输失败)", () => {
  it("200 + ok → ok", () => {
    expect(loginOutcome(200, true)).toBe("ok");
  });

  it("401 → wrong-secret", () => {
    expect(loginOutcome(401, false)).toBe("wrong-secret");
  });

  it("传输失败（0）→ unreachable", () => {
    expect(loginOutcome(0, false)).toBe("unreachable");
  });

  it("服务端 5xx → unreachable，不冤枉口令", () => {
    expect(loginOutcome(500, false)).toBe("unreachable");
    expect(loginOutcome(503, false)).toBe("unreachable");
  });

  it("200 但信封不 ok → unreachable（不当成口令错误）", () => {
    expect(loginOutcome(200, false)).toBe("unreachable");
  });
});
