import { describe, expect, it } from "vitest";
import { nextPlanVersion, shouldReloadOnPlanVersion } from "./planVersion";

describe("nextPlanVersion (#256 max 语义)", () => {
  it("takes the max in both directions", () => {
    expect(nextPlanVersion(3, 5)).toBe(5);
    expect(nextPlanVersion(5, 3)).toBe(5);
    expect(nextPlanVersion(4, 4)).toBe(4);
  });
});

describe("shouldReloadOnPlanVersion (#258 外部版本同步)", () => {
  it("reloads only when the stored version moved ahead", () => {
    expect(shouldReloadOnPlanVersion(3, 3)).toBe(false);
    expect(shouldReloadOnPlanVersion(3, 4)).toBe(true);
    expect(shouldReloadOnPlanVersion(4, 3)).toBe(false);
  });
});
