import { describe, expect, it } from "vitest";
import { ChatTurn } from "../types";
import { isPlanNotice, splitRounds, stepsRoundIndex } from "./rounds";

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

describe("stepsRoundIndex", () => {
  it("步骤块挂在最后一条用户回合所在的轮", () => {
    const rounds = splitRounds([
      { role: "user", content: "一" },
      { role: "assistant", content: "答" },
      { role: "user", content: "二" },
      { role: "assistant", content: "答" },
    ]);
    expect(stepsRoundIndex(rounds)).toBe(1);
  });

  it("没有用户回合时退回最后一轮", () => {
    const rounds = splitRounds([{ role: "assistant", content: "开场" }]);
    expect(stepsRoundIndex(rounds)).toBe(0);
  });
});

describe("isPlanNotice", () => {
  // 协议：后端 _plan_note_text 恒为「计划已更新到第 N 版。」（nodes.py:75-79）。
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
