import { describe, expect, it } from "vitest";
import {
  nextPlanVersion,
  shouldBumpListRevision,
  shouldRefetchParked,
} from "./requestPlan";

describe("shouldRefetchParked (#258 AC4 / D-30)", () => {
  it("does not refetch when the first status comes from a snapshot", () => {
    expect(shouldRefetchParked(null, "t1", "INTERRUPTED")).toBe(false);
    expect(shouldRefetchParked(null, "t1", "FAILED")).toBe(false);
  });

  it("refetches on a same-task transition into parked", () => {
    expect(shouldRefetchParked({ id: "t1", status: "RUNNING" }, "t1", "INTERRUPTED")).toBe(
      true
    );
    expect(shouldRefetchParked({ id: "t1", status: "PENDING" }, "t1", "FAILED")).toBe(true);
  });

  it("ignores another task's remembered status (switch race)", () => {
    expect(shouldRefetchParked({ id: "t1", status: "RUNNING" }, "t2", "INTERRUPTED")).toBe(
      false
    );
  });

  it("does not refetch between parked states or between live states", () => {
    expect(
      shouldRefetchParked({ id: "t1", status: "INTERRUPTED" }, "t1", "INTERRUPTED")
    ).toBe(false);
    expect(shouldRefetchParked({ id: "t1", status: "FAILED" }, "t1", "INTERRUPTED")).toBe(
      false
    );
    expect(shouldRefetchParked({ id: "t1", status: "RUNNING" }, "t1", "RUNNING")).toBe(false);
  });
});

describe("nextPlanVersion (#256 max 语义)", () => {
  it("takes the max in both directions", () => {
    expect(nextPlanVersion(3, 5)).toBe(5);
    expect(nextPlanVersion(5, 3)).toBe(5);
    expect(nextPlanVersion(4, 4)).toBe(4);
  });
});

describe("shouldBumpListRevision (#258 AC4)", () => {
  it("bumps only for a genuinely new id", () => {
    expect(shouldBumpListRevision(["t1", "t2"], "t3")).toBe(true);
    expect(shouldBumpListRevision(["t1", "t2"], "t2")).toBe(false);
    expect(shouldBumpListRevision([], undefined)).toBe(false);
  });
});