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
  it("计划更新提示走静音行", () => {
    expect(isPlanNotice("  计划已更新到 v3")).toBe(true);
  });

  it("普通回答不误判", () => {
    expect(isPlanNotice("计划如下：先做 A")).toBe(false);
  });
});
