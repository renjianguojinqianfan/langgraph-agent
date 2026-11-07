/** The minimal element surface the ledger touches; `HTMLElement` satisfies it.
 * Kept structural so the ledger can be unit-tested with plain mocks (D-23: no
 * jsdom in this batch). */
export interface InertElement {
  inert: boolean;
  getAttribute(name: string): string | null;
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
}

interface SavedState {
  inert: boolean;
  ariaHidden: string | null;
}

export interface InertLedger {
  /** Set `inert` (+ mirrored `aria-hidden`) on `el`, remembering the state it
   * had when first touched. No-op for `null`. */
  apply(el: InertElement | null, inert: boolean): void;
  /** Put every touched element back to its first-seen state, then forget it. */
  restoreAll(): void;
}

/** A small write-ahead ledger for `inert`/`aria-hidden` (D-27 / #257).
 *
 * The hook used to restore from its *current* target lists, but those lists are
 * recomputed on the closing render (background targets become `[]`), so the
 * marks applied while open were never withdrawn. The ledger remembers what each
 * element looked like before we touched it, so "restore" is independent of the
 * state that happens to be current. */
export function createInertLedger(): InertLedger {
  const saved = new Map<InertElement, SavedState>();

  return {
    apply(el, inert) {
      if (!el) return;
      if (!saved.has(el)) {
        saved.set(el, {
          inert: el.inert,
          ariaHidden: el.getAttribute("aria-hidden"),
        });
      }
      el.inert = inert;
      if (inert) el.setAttribute("aria-hidden", "true");
      else el.removeAttribute("aria-hidden");
    },

    restoreAll() {
      for (const [el, original] of saved) {
        el.inert = original.inert;
        if (original.ariaHidden === null) el.removeAttribute("aria-hidden");
        else el.setAttribute("aria-hidden", original.ariaHidden);
      }
      saved.clear();
    },
  };
}