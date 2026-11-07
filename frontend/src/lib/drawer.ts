/** Which side is currently a narrow-screen drawer (D-26 / #257). */
export type DrawerSide = "left" | "right";

export interface DrawerViewInput {
  /** `width < 860px`: the left rail is a drawer. */
  leftNarrow: boolean;
  /** `width < 1180px`: the right dash is a drawer. */
  rightNarrow: boolean;
  /** At most one drawer is open at a time — mutual exclusion is this entry. */
  open: DrawerSide | null;
  /** The Trace replay face owns the center column; drawers step aside. */
  traceOpen: boolean;
}

export interface DrawerView {
  /** The scrim is visible exactly when a drawer is actually justified. */
  overlayVisible: boolean;
  /** Main content and the other column are inert while a drawer is justified. */
  backgroundInert: boolean;
  /** `open` names a side that stopped being narrow — close it (no orphan scrim). */
  closeOnWiden: boolean;
  /** Trace opened while a drawer was open — close the drawer. */
  closeForTrace: boolean;
  /** The side that should render as the drawer right now, or null. */
  justify: DrawerSide | null;
}

/** Derive every drawer-facing bit from one open entry and the two breakpoints.
 *
 * `overlayVisible` is deliberately *not* decided by `open` alone: a drawer that
 * stopped being narrow (or that is hidden behind the Trace face) must not leave
 * a scrim behind. `justify` is the single answer the view and the focus layer
 * both read. */
export function drawerView({
  leftNarrow,
  rightNarrow,
  open,
  traceOpen,
}: DrawerViewInput): DrawerView {
  const openNarrow =
    open === "left" ? leftNarrow : open === "right" ? rightNarrow : false;
  const closeOnWiden = open !== null && !openNarrow;
  const closeForTrace = traceOpen && open !== null;
  const justify = openNarrow && !traceOpen ? open : null;
  const overlayVisible = justify !== null;

  return {
    overlayVisible,
    backgroundInert: overlayVisible,
    closeOnWiden,
    closeForTrace,
    justify,
  };
}