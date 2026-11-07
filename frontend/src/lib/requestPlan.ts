import type { TaskStatus } from "../types";
import { isParked } from "./taskState";

/** What the parked effect remembers between runs: the task it observed and
 * that task's last status. Keying by id is load-bearing: switching from a
 * running task to another task that is already parked must not read the
 * *previous* task's RUNNING as a live→parked transition. */
export interface ParkedStatusMark {
  id: string;
  status: string;
}

/** D-30 / #258 AC4: does a status change need the authoritative "round parked"
 * `getTask`? Only a same-task transition INTO parked from a live state does.
 * The status that comes with a fresh `getTaskSnapshot` already is the
 * authoritative one, so selecting an already-parked task (prev === null) must
 * not refetch, parked → parked must not, and another task's mark never counts. */
export function shouldRefetchParked(
  prev: ParkedStatusMark | null,
  taskId: string,
  nextStatus: string
): boolean {
  if (prev === null || prev.id !== taskId) return false;
  return !isParked(prev.status as TaskStatus) && isParked(nextStatus as TaskStatus);
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