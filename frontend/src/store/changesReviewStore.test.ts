import { beforeEach, describe, expect, it } from "vitest";
import { reviewKey } from "../lib/changes";
import { useChangesReviewStore } from "./changesReviewStore";

describe("changes review store", () => {
  beforeEach(() => {
    useChangesReviewStore.getState().clearAll();
  });

  it("keeps reviewed paths per (task, round) key", () => {
    const state = useChangesReviewStore.getState();
    const roundOne = reviewKey("task-1", 1);
    const roundTwo = reviewKey("task-1", 2);
    state.toggle(roundOne, "a.ts");
    state.toggle(roundOne, "b.ts");
    state.toggle(roundTwo, "a.ts");

    expect(useChangesReviewStore.getState().reviewed[roundOne]).toEqual([
      "a.ts",
      "b.ts",
    ]);
    expect(useChangesReviewStore.getState().reviewed[roundTwo]).toEqual(["a.ts"]);
    expect(useChangesReviewStore.getState().reviewed[roundOne]).toContain("a.ts");
    expect(useChangesReviewStore.getState().reviewed[roundOne]).not.toContain("c.ts");
  });

  it("toggles a marker off without touching its neighbours", () => {
    const key = reviewKey("task-1", 1);
    const state = useChangesReviewStore.getState();
    state.toggle(key, "a.ts");
    state.toggle(key, "b.ts");
    state.toggle(key, "a.ts");
    expect(useChangesReviewStore.getState().reviewed[key]).toEqual(["b.ts"]);
  });

  it("clears every task when the selected task changes", () => {
    const state = useChangesReviewStore.getState();
    state.toggle(reviewKey("task-1", 1), "a.ts");
    state.toggle(reviewKey("task-2", 1), "b.ts");
    state.clearAll();
    expect(useChangesReviewStore.getState().reviewed).toEqual({});
    expect(
      useChangesReviewStore.getState().reviewed[reviewKey("task-1", 1)]
    ).toBeUndefined();
  });
});
