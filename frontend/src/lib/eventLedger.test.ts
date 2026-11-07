import { describe, expect, it } from "vitest";
import type { SSEvent } from "../types";
import { isLedgerType, ledgerEvents, LEDGER_LIMIT } from "./eventLedger";

function ev(type: SSEvent["type"], data: unknown, ts = 1): SSEvent {
  return { type, data, ts };
}

describe("isLedgerType (D-29)", () => {
  it("excludes heartbeat only", () => {
    expect(isLedgerType("heartbeat")).toBe(false);
    expect(isLedgerType("task_created")).toBe(true);
    expect(isLedgerType("step_start")).toBe(true);
    expect(isLedgerType("final_answer")).toBe(true);
  });
});

describe("ledgerEvents (D-29)", () => {
  it("empty list stays empty", () => {
    expect(ledgerEvents([])).toEqual([]);
  });

  it("dedupes the same payload and keeps the latest arrival", () => {
    const first = ev("step_start", { task_id: "t1", index: 1 }, 1);
    const replay = ev("step_start", { task_id: "t1", index: 1 }, 9);
    const out = ledgerEvents([first, replay]);
    expect(out).toHaveLength(1);
    expect(out[0]).toBe(replay);
  });

  it("keeps distinct payloads in order and renders newest first", () => {
    const a = ev("step_start", { task_id: "t1", index: 1 });
    const b = ev("tool_call", { task_id: "t1", tool_name: "x" });
    const c = ev("final_answer", { task_id: "t1", answer: "ok" });
    expect(ledgerEvents([a, b, c]).map((e) => e.type)).toEqual([
      "final_answer",
      "tool_call",
      "step_start",
    ]);
  });

  it("filters heartbeat out of the ledger", () => {
    const out = ledgerEvents([
      ev("heartbeat", {}),
      ev("step_start", { task_id: "t1", index: 1 }),
      ev("heartbeat", {}),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].type).toBe("step_start");
  });

  it("caps at the ledger window, keeping the newest", () => {
    expect(LEDGER_LIMIT).toBe(100);
    const all = Array.from({ length: 105 }, (_, i) =>
      ev("step_start", { task_id: "t1", index: i })
    );
    const out = ledgerEvents(all);
    expect(out).toHaveLength(100);
    expect((out[0].data as { index: number }).index).toBe(104);
    expect((out[99].data as { index: number }).index).toBe(5);
  });

  it("accepts an explicit window size", () => {
    const all = Array.from({ length: 5 }, (_, i) =>
      ev("step_start", { task_id: "t1", index: i })
    );
    const out = ledgerEvents(all, 2);
    expect(out).toHaveLength(2);
    expect((out[0].data as { index: number }).index).toBe(4);
  });
});