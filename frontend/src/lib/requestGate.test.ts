import { describe, expect, it } from "vitest";
import { createRequestGate } from "./requestGate";

describe("createRequestGate", () => {
  it("keeps the newest request current and drops an older one", () => {
    const gate = createRequestGate();
    const first = gate.begin();
    const second = gate.begin();
    expect(gate.isCurrent(first)).toBe(false);
    expect(gate.isCurrent(second)).toBe(true);
  });

  it("invalidates a pending request when the dialog closes", () => {
    const gate = createRequestGate();
    const token = gate.begin();
    gate.invalidate();
    expect(gate.isCurrent(token)).toBe(false);
  });

  it("accepts a request started after a close", () => {
    const gate = createRequestGate();
    gate.invalidate();
    const token = gate.begin();
    expect(gate.isCurrent(token)).toBe(true);
  });
});
