import type { StepRecord, TraceMarker } from "../types";

export interface MarkerGroups {
  /** Live markers owned by a step, keyed by that step's index. */
  byStep: Map<number, TraceMarker[]>;
  /** Markers no current step owns; they render once outside the round blocks. */
  loose: TraceMarker[];
}

/**
 * Resolve each live marker to the step it belongs to (#265). Ownership is a
 * property of the task snapshot, not of a round block, so it is computed once
 * in Conversation; a marker whose step is missing stays loose instead of being
 * guessed into the newest round.
 */
export function groupMarkers(
  markers: TraceMarker[],
  steps: StepRecord[]
): MarkerGroups {
  const indexes = new Set(steps.map((step) => step.index));
  const byStep = new Map<number, TraceMarker[]>();
  const loose: TraceMarker[] = [];
  for (const marker of markers) {
    const index = marker.data.step_index;
    if (indexes.has(index)) {
      byStep.set(index, [...(byStep.get(index) ?? []), marker]);
    } else {
      loose.push(marker);
    }
  }
  return { byStep, loose };
}
