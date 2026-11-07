import { describe, expect, it } from "vitest";
import { createInertLedger, type InertElement } from "./inertLedger";

class FakeElement implements InertElement {
  inert = false;
  readonly attrs = new Map<string, string>();

  getAttribute(name: string): string | null {
    return this.attrs.get(name) ?? null;
  }

  setAttribute(name: string, value: string): void {
    this.attrs.set(name, value);
  }

  removeAttribute(name: string): void {
    this.attrs.delete(name);
  }
}

// HIGH 回归（PR #293 评审）：关闭时背景 inert 必须撤销，且与「当前 target 列表」
// 无关——账本记下首次触碰前的原值，restoreAll 恢复它。
describe("createInertLedger", () => {
  it("apply(true) then restoreAll restores a pristine element", () => {
    const el = new FakeElement();
    const ledger = createInertLedger();

    ledger.apply(el, true);
    expect(el.inert).toBe(true);
    expect(el.getAttribute("aria-hidden")).toBe("true");

    ledger.restoreAll();
    expect(el.inert).toBe(false);
    expect(el.getAttribute("aria-hidden")).toBeNull();
  });

  it("apply(false) then restoreAll restores the original inert=true", () => {
    const el = new FakeElement();
    el.inert = true;
    el.setAttribute("aria-hidden", "true");
    const ledger = createInertLedger();

    ledger.apply(el, false);
    expect(el.inert).toBe(false);
    expect(el.getAttribute("aria-hidden")).toBeNull();

    ledger.restoreAll();
    expect(el.inert).toBe(true);
    expect(el.getAttribute("aria-hidden")).toBe("true");
  });

  it("restores a pre-existing aria-hidden value verbatim", () => {
    const el = new FakeElement();
    el.setAttribute("aria-hidden", "false");
    const ledger = createInertLedger();

    ledger.apply(el, true);
    ledger.restoreAll();
    expect(el.getAttribute("aria-hidden")).toBe("false");
  });

  it("repeated apply on one element remembers the first-seen state only", () => {
    const el = new FakeElement();
    const ledger = createInertLedger();

    ledger.apply(el, true);
    ledger.apply(el, false);
    ledger.apply(el, true);
    ledger.restoreAll();
    expect(el.inert).toBe(false);
    expect(el.getAttribute("aria-hidden")).toBeNull();
  });

  it("an empty ledger restores nothing (no throw, no side effects)", () => {
    const ledger = createInertLedger();
    expect(() => ledger.restoreAll()).not.toThrow();
    const el = new FakeElement();
    expect(el.inert).toBe(false);
    expect(el.getAttribute("aria-hidden")).toBeNull();
  });
});