import { describe, expect, it } from "vitest";
import { nextPlanVersion } from "./planVersion";

describe("nextPlanVersion (#256 max 语义)", () => {
  it("takes the max in both directions", () => {
    expect(nextPlanVersion(3, 5)).toBe(5);
    expect(nextPlanVersion(5, 3)).toBe(5);
    expect(nextPlanVersion(4, 4)).toBe(4);
  });
});
