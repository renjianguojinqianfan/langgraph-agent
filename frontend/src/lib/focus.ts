/** Everything the drawer focus trap treats as focusable (D-27 / #257).
 *
 * Exported so the hook and the tests share one definition; `tabindex="-1"`
 * containers are excluded on purpose (the fallback container is not a stop). */
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** What a keydown means inside an open drawer (D-27 keyboard map).
 *
 * Kept as a pure function so the Esc/Tab contract has a mechanical test even
 * without a DOM stack (D-23); the hook only dispatches on the result. */
export type DrawerKeyAction =
  | "close"
  | "cycle-backward"
  | "cycle-forward"
  | null;

export function drawerKeyAction(
  key: string,
  shiftKey: boolean
): DrawerKeyAction {
  if (key === "Escape") return "close";
  if (key === "Tab") return shiftKey ? "cycle-backward" : "cycle-forward";
  return null;
}

/** Index Tab / Shift+Tab moves to inside a trapped list of `count` elements.
 *
 * Wraps at both ends; a stale/absent `currentIndex` (not in `[0, count)`, e.g.
 * focus was on the container itself) enters from the matching edge; an empty
 * list yields -1 — the caller has nothing to focus. */
export function nextFocusIndex(
  count: number,
  currentIndex: number,
  shiftKey: boolean
): number {
  if (count <= 0) return -1;
  if (currentIndex < 0 || currentIndex >= count) {
    return shiftKey ? count - 1 : 0;
  }
  return shiftKey
    ? (currentIndex - 1 + count) % count
    : (currentIndex + 1) % count;
}
