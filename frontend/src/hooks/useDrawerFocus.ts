import { useEffect, useRef, type RefObject } from "react";
import { drawerKeyAction, FOCUSABLE_SELECTOR, nextFocusIndex } from "../lib/focus";

/** A region whose inert state is not `drawerView().backgroundInert`: the
 * off-screen drawer panels when the screen is narrow but nothing is open.
 * TaskView derives the boolean from the same drawerView bits; the hook stays
 * the only writer of the DOM attributes (D-27 单一写入点). */
export interface InertEdge {
  ref: RefObject<HTMLElement | null>;
  inert: boolean;
}

export interface UseDrawerFocusOptions {
  /** D-26 `justify !== null`: a drawer is actually justified (narrow + open). */
  active: boolean;
  /** The justified drawer element. Inactive renders point at either side. */
  containerRef: RefObject<HTMLElement | null>;
  /** Background regions that go inert while a drawer is justified: center
   * column, the other aside, the skip link. */
  inertTargets: Array<RefObject<HTMLElement | null>>;
  /** `drawerView(...).backgroundInert` — the single source of the background state. */
  backgroundInert: boolean;
  /** Closed off-screen drawers (narrow but not justified). */
  edges?: InertEdge[];
  onClose: () => void;
}

function setInert(el: HTMLElement | null, inert: boolean): void {
  if (!el) return;
  el.inert = inert;
  if (inert) el.setAttribute("aria-hidden", "true");
  else el.removeAttribute("aria-hidden");
}

function focusablesIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

/** D-27 focus/keyboard management for the narrow-screen drawers (#257).
 *
 * Active means `justify !== null` (D-26), not merely `open`: a drawer that
 * stopped being narrow or hid behind Trace never traps focus. On open the
 * trigger element is recorded once, focus moves to the first focusable child
 * (or the container itself), Escape closes, Tab/Shift+Tab cycle inside, and
 * focus that escapes is pulled back. On close the inert/aria-hidden marks are
 * withdrawn *before* focus returns to the trigger — order is load-bearing. */
export function useDrawerFocus({
  active,
  containerRef,
  inertTargets,
  backgroundInert,
  edges = [],
  onClose,
}: UseDrawerFocusOptions): void {
  const triggerRef = useRef<HTMLElement | null>(null);
  // Latest values for the installed listeners/cleanup without re-running the
  // open/close effect (which would re-record the trigger and steal focus).
  const inertTargetsRef = useRef(inertTargets);
  const edgesRef = useRef(edges);
  const backgroundInertRef = useRef(backgroundInert);
  const onCloseRef = useRef(onClose);
  inertTargetsRef.current = inertTargets;
  edgesRef.current = edges;
  backgroundInertRef.current = backgroundInert;
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    // Record the element that opened the drawer exactly once, on entry.
    triggerRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const first = focusablesIn(container)[0];
    let temporaryTabIndex = false;
    if (first) first.focus();
    else {
      // Nothing focusable inside: make the container itself the stop.
      container.tabIndex = -1;
      temporaryTabIndex = true;
      container.focus();
    }

    const onKeyDown = (ev: KeyboardEvent) => {
      const action = drawerKeyAction(ev.key, ev.shiftKey);
      if (action === "close") {
        ev.preventDefault();
        onCloseRef.current();
        return;
      }
      if (action === null) return;
      const items = focusablesIn(container);
      const current =
        document.activeElement instanceof HTMLElement
          ? items.indexOf(document.activeElement)
          : -1;
      const target = nextFocusIndex(items.length, current, ev.shiftKey);
      if (target >= 0) {
        ev.preventDefault();
        items[target].focus();
      }
    };

    const onFocusIn = (ev: FocusEvent) => {
      const target = ev.target as Node | null;
      if (target && container.contains(target)) return;
      const items = focusablesIn(container);
      (items[0] ?? container).focus();
    };

    // Capture phase: see Tab before per-widget handlers can consume it.
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("focusin", onFocusIn, true);

    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("focusin", onFocusIn, true);
      // D-27 order: withdraw inert/aria-hidden first, then hand focus back.
      for (const ref of inertTargetsRef.current) setInert(ref.current, false);
      for (const edge of edgesRef.current) setInert(edge.ref.current, false);
      const trigger = triggerRef.current;
      if (trigger && trigger.isConnected) trigger.focus();
      triggerRef.current = null;
      if (temporaryTabIndex) container.removeAttribute("tabindex");
    };
  }, [active, containerRef]);

  // Single writer of the `inert` marks (D-27): every render applies the latest
  // derived state, and it runs after the focus cleanup above, so the closed
  // state (edges inert on narrow screens) is restored in the same commit.
  useEffect(() => {
    for (const ref of inertTargetsRef.current) {
      setInert(ref.current, backgroundInertRef.current);
    }
    for (const edge of edgesRef.current) setInert(edge.ref.current, edge.inert);
  });
}
