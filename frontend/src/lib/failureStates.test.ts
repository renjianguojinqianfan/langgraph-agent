import { describe, expect, it } from "vitest";
import {
  detailLoadFailure,
  detailLoadState,
  historyListState,
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
  it("200 → ready", () => {
    expect(detailLoadState(200)).toBe("ready");
  });

  it("404 → missing", () => {
    expect(detailLoadState(404)).toBe("missing");
  });

  it("传输失败（0）与服务端 5xx → unreachable", () => {
    expect(detailLoadState(0)).toBe("unreachable");
    expect(detailLoadState(500)).toBe("unreachable");
    expect(detailLoadState(502)).toBe("unreachable");
  });

  it("两种失败各有文案，都不说「新任务」", () => {
    expect(detailLoadFailure("missing")).toContain("不存在");
    expect(detailLoadFailure("unreachable")).toContain("无法连接后台");
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
