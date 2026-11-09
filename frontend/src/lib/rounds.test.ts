import { describe, expect, it } from "vitest";
import { ChatTurn, StepRecord } from "../types";
import {
  isPlanNotice,
  legacySteps,
  splitRounds,
  stepsForRound,
  turnVerdict,
} from "./rounds";

describe("splitRounds", () => {
  it("空对话 → 无轮次", () => {
    expect(splitRounds([])).toEqual([]);
  });

  it("用户起轮，助手回答归入当前轮", () => {
    const turns: ChatTurn[] = [
      { role: "user", content: "第一句" },
      { role: "assistant", content: "第一答" },
      { role: "user", content: "第二句" },
      { role: "assistant", content: "第二答" },
    ];
    expect(splitRounds(turns)).toEqual([
      {
        index: 1,
        user: { role: "user", content: "第一句" },
        answers: [{ role: "assistant", content: "第一答" }],
      },
      {
        index: 2,
        user: { role: "user", content: "第二句" },
        answers: [{ role: "assistant", content: "第二答" }],
      },
    ]);
  });

  it("用户之前的助手回合保留为无用户气泡的首轮", () => {
    const rounds = splitRounds([
      { role: "assistant", content: "开场" },
      { role: "user", content: "提问" },
    ]);
    expect(rounds).toHaveLength(2);
    expect(rounds[0].user).toBeNull();
    expect(rounds[0].answers[0].content).toBe("开场");
    // #265: the leading block must not shift the backend's user-turn numbering.
    expect(rounds[0].index).toBe(0);
    expect(rounds[1].index).toBe(1);
    expect(rounds[1].user?.content).toBe("提问");
  });

  it("同轮多条助手回合全部保留", () => {
    const rounds = splitRounds([
      { role: "user", content: "问" },
      { role: "assistant", content: "答一" },
      { role: "assistant", content: "答二" },
    ]);
    expect(rounds[0].answers.map((a) => a.content)).toEqual(["答一", "答二"]);
  });
});

function makeStep(index: number, round?: number, status = "done"): StepRecord {
  return {
    index,
    ...(round !== undefined ? { round } : {}),
    thought: "",
    tool_calls: [],
    status,
  };
}

// #265 replaces the old `stepsRoundIndex` guess: steps now carry their own
// round, so the two "which round owns the block" cases were deleted with the
// helper. `Round.index` itself is still covered by the splitRounds cases above.
describe("stepsForRound / legacySteps", () => {
  const steps = [makeStep(1, 1), makeStep(2, 2), makeStep(3, 0), makeStep(4)];

  it("只取指定轮的步骤", () => {
    expect(stepsForRound(steps, 1).map((s) => s.index)).toEqual([1]);
    expect(stepsForRound(steps, 2).map((s) => s.index)).toEqual([2]);
  });

  it("没有对应轮次时返回空数组", () => {
    expect(stepsForRound(steps, 9)).toEqual([]);
  });

  it("旧记录（round=0 或缺失）单独归为 legacy，不猜轮次", () => {
    expect(legacySteps(steps).map((s) => s.index)).toEqual([3, 4]);
  });
});

describe("turnVerdict", () => {
  it("崩溃后续跑完成：末步 done，前面失败的断点步不改写本轮结论", () => {
    expect(turnVerdict([makeStep(1, 1, "failed"), makeStep(2, 1)], false)).toBe("done");
  });

  it("末步失败才是处理失败", () => {
    expect(turnVerdict([makeStep(1, 1), makeStep(2, 1, "failed")], false)).toBe("failed");
    expect(turnVerdict([makeStep(1, 1, "failed")], false)).toBe("failed");
  });

  it("运行中的轮次保持执行中，即使末步已收尾", () => {
    expect(turnVerdict([makeStep(1, 1)], true)).toBe("running");
  });

  it("运行中末步已失败则显示处理失败", () => {
    expect(turnVerdict([makeStep(1, 1, "failed")], true)).toBe("failed");
  });

  it("末步 running 或旧记录残留 running 按执行中兜底", () => {
    expect(turnVerdict([makeStep(1, 1, "running")], false)).toBe("running");
    expect(
      turnVerdict([makeStep(1, 1, "running"), makeStep(2, 1)], false),
    ).toBe("running");
  });
});

describe("isPlanNotice", () => {
  // 协议：后端 plan_file.plan_note_text 恒为「计划已更新到第 N 版。」
  // （经 nodes.py 的 _plan_note_text 产出）。
  it("计划更新提示走静音行", () => {
    expect(isPlanNotice("计划已更新到第 3 版。")).toBe(true);
    expect(isPlanNotice("  计划已更新到第 2 版。\n")).toBe(true);
  });

  // #231：前缀相同但后面还有正文，是回答而不是提示，不能被静音行吞掉。
  it("同前缀的正文不误判为提示", () => {
    expect(isPlanNotice("计划已更新到第 3 版后，建议先跑测试。")).toBe(false);
  });

  it("普通回答不误判", () => {
    expect(isPlanNotice("计划如下：先做 A")).toBe(false);
    expect(isPlanNotice("计划已更新到 v3")).toBe(false);
  });
});
