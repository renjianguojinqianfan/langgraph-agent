/** #346 layout C: a floating navigator, never a hard split.
 *
 * The right column is 420 on desktop; a bare `row < 400` test would call that
 * narrow, so the two rules are independent:
 * - row < 400 or viewport < 768 → the tree fills the panel below the preview
 *   header (the preview shows when the tree is closed);
 * - 400 ≤ row < 560 → the tree floats over the preview (default 260, min 180,
 *   max min(300, row - 120)); the preview keeps its full width;
 * - row ≥ 560 → the tree is an edge-docked raised panel and the preview body
 *   keeps at least 320 (max min(300, row - 320)).
 *
 * Pure so the breakpoint/clamp contract is testable without a DOM (D-23). */
export type NavigatorMode = "float" | "side" | "fill";

export interface NavigatorLayout {
  mode: NavigatorMode;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  /** False in fill mode: the tree owns the whole row, there is no split. */
  resizable: boolean;
}

const FILL_ROW_MAX = 400;
const SIDE_ROW_MIN = 560;
const FILL_VIEWPORT_MAX = 768;
const PREFERRED_WIDTH = 260;
const MIN_WIDTH = 180;
const MAX_WIDTH = 300;
const FLOAT_RESERVED = 120;
const SIDE_BODY_MIN = 320;

/** One ArrowLeft/ArrowRight nudge on the width separator. */
export const NAVIGATOR_KEY_STEP = 16;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function navigatorLayout(
  rowWidth: number,
  viewportWidth: number
): NavigatorLayout {
  const row = Math.max(0, Math.floor(rowWidth));

  if (row < FILL_ROW_MAX || viewportWidth < FILL_VIEWPORT_MAX) {
    return {
      mode: "fill",
      defaultWidth: row,
      minWidth: row,
      maxWidth: row,
      resizable: false,
    };
  }

  const side = row >= SIDE_ROW_MIN;
  const maxWidth = Math.min(
    MAX_WIDTH,
    Math.max(MIN_WIDTH, row - (side ? SIDE_BODY_MIN : FLOAT_RESERVED))
  );
  return {
    mode: side ? "side" : "float",
    defaultWidth: clamp(PREFERRED_WIDTH, MIN_WIDTH, maxWidth),
    minWidth: MIN_WIDTH,
    maxWidth,
    resizable: true,
  };
}

/** Clamp a remembered/dragged width to the current mode's split. */
export function clampNavigatorWidth(
  width: number,
  layout: NavigatorLayout
): number {
  if (!Number.isFinite(width)) return layout.defaultWidth;
  return clamp(Math.round(width), layout.minWidth, layout.maxWidth);
}

/** Keyboard width step for the separator; null leaves the key alone. */
export function navigatorKeyWidth(
  width: number,
  key: string,
  layout: NavigatorLayout
): number | null {
  switch (key) {
    case "ArrowLeft":
      return clampNavigatorWidth(width - NAVIGATOR_KEY_STEP, layout);
    case "ArrowRight":
      return clampNavigatorWidth(width + NAVIGATOR_KEY_STEP, layout);
    case "Home":
      return layout.minWidth;
    case "End":
      return layout.maxWidth;
    default:
      return null;
  }
}
