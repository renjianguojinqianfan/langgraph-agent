import { describe, expect, it } from "vitest";
import { StepRecord, TraceMarker } from "../types";
import { groupMarkers } from "./markers";

function marker(stepIndex: number, dropped = 1): TraceMarker {
  return {
    type: "context_compressed",
    data: {
      step_index: stepIndex,
      dropped,
      context_tokens: 100,
      strategy: "truncate",
    },
  };
}

function step(index: number, round?: number): StepRecord {
  return {
    index,
    ...(round !== undefined ? { round } : {}),
    thought: "",
    tool_calls: [],
    status: "done",
  };
}

describe("groupMarkers", () => {
  it("按步骤认领标记，同一歩可有多条", () => {
    const groups = groupMarkers(
      [marker(2), marker(1), marker(2, 3)],
      [step(1, 1), step(2, 2)]
    );
    expect(groups.byStep.get(1)).toHaveLength(1);
    expect(groups.byStep.get(2)?.map((m) => m.data.dropped)).toEqual([1, 3]);
    expect(groups.loose).toEqual([]);
  });

  it("找不到所属步骤的标记不猜轮次，归为 loose", () => {
    const groups = groupMarkers([marker(9)], [step(1, 1)]);
    expect(groups.byStep.size).toBe(0);
    expect(groups.loose).toHaveLength(1);
  });

  it("旧记录步骤（无 round）仍然认领自己的标记", () => {
    const groups = groupMarkers([marker(3)], [step(3)]);
    expect(groups.byStep.get(3)).toHaveLength(1);
    expect(groups.loose).toEqual([]);
  });
});
