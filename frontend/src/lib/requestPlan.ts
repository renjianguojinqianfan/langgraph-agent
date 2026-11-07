import type { TaskStatus } from "../types";
import { isParked } from "./taskState";

/** D-30 / #258 AC4: does a status change need the authoritative "round parked"
 * `getTask`? Only a transition INTO parked from a live state does. The status
 * that comes with a fresh `getTaskSnapshot` already is the authoritative one,
 * so selecting an already-parked task (prev === null → parked) must not refetch,
 * and parked → parked must not either. */
export function shouldRefetchParked(
  prevStatus: string | null,
  nextStatus: string
): boolean {
  if (prevStatus === null) return false;
  return !isParked(prevStatus as TaskStatus) && isParked(nextStatus as TaskStatus);
}

/** #256 plan-version merge, extracted so "only ever grows" is testable. */
export function nextPlanVersion(current: number, incoming: number): number {
  return Math.max(current, incoming);
}

/** D-30 / #258 AC4: only a `task_created` for an id the list does not know yet
 * may bump `listRevision`; a replayed event for a known task must not refetch
 * the history. */
export function shouldBumpListRevision(
  knownTaskIds: readonly string[],
  taskId: string | undefined
): boolean {
  return !!taskId && !knownTaskIds.includes(taskId);
}