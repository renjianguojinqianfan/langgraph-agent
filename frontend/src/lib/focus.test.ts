import { describe, expect, it } from "vitest";
import { drawerKeyAction, FOCUSABLE_SELECTOR, nextFocusIndex } from "./focus";

describe("nextFocusIndex (D-27 Tab trap)", () => {
  it("forward wraps at the tail", () => {
    expect(nextFocusIndex(3, 0, false)).toBe(1);
    expect(nextFocusIndex(3, 2, false)).toBe(0);
  });

  it("backward wraps at the head", () => {
    expect(nextFocusIndex(3, 2, true)).toBe(1);
    expect(nextFocusIndex(3, 0, true)).toBe(2);
  });

  it("single element stays on itself in both directions", () => {
    expect(nextFocusIndex(1, 0, false)).toBe(0);
    expect(nextFocusIndex(1, 0, true)).toBe(0);
  });

  it("empty list has nothing to focus", () => {
    expect(nextFocusIndex(0, 0, false)).toBe(-1);
    expect(nextFocusIndex(0, 0, true)).toBe(-1);
  });

  it("stale current index enters from the matching edge", () => {
    expect(nextFocusIndex(3, -1, false)).toBe(0);
    expect(nextFocusIndex(3, -1, true)).toBe(2);
    expect(nextFocusIndex(3, 99, false)).toBe(0);
    expect(nextFocusIndex(3, 99, true)).toBe(2);
  });

  it("selector matches the D-27 contract verbatim", () => {
    expect(FOCUSABLE_SELECTOR).toBe(
      'a[href], button:not([disabled]), textarea:not([disabled]), ' +
        'input:not([disabled]), select:not([disabled]), ' +
        '[tabindex]:not([tabindex="-1"])'
    );
  });
});

describe("drawerKeyAction (D-27 keyboard map)", () => {
  it("Escape closes the drawer", () => {
    expect(drawerKeyAction("Escape", false)).toBe("close");
    expect(drawerKeyAction("Escape", true)).toBe("close");
  });

  it("Tab cycles forward", () => {
    expect(drawerKeyAction("Tab", false)).toBe("cycle-forward");
  });

  it("Shift+Tab cycles backward", () => {
    expect(drawerKeyAction("Tab", true)).toBe("cycle-backward");
  });

  it("other keys are ignored", () => {
    expect(drawerKeyAction("Enter", false)).toBeNull();
    expect(drawerKeyAction("a", true)).toBeNull();
  });
});
