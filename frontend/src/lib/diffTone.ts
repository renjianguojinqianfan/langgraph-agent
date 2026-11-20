import type { DiffKind } from "./changes";

/**
 * One tone table for every diff surface (#343): the hover preview, the unified
 * sign column and the split gutters. The unified body dims context less so it
 * stays readable; one base table keeps the surfaces from drifting apart.
 */
export const DIFF_TONE: Record<DiffKind, string> = {
  add: "text-state-ok",
  delete: "text-state-danger",
  context: "text-tertiary",
};

/** Unified body text: context uses `secondary` rather than the sign tone. */
export const DIFF_BODY_TONE: Record<DiffKind, string> = {
  ...DIFF_TONE,
  context: "text-secondary",
};
