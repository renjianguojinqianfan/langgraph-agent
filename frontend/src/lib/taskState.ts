import type { TaskStatus } from "../types";

/** Parked = the round ended and the next message continues the same thread. */
export function isParked(status: TaskStatus): boolean {
  return status === "INTERRUPTED" || status === "FAILED";
}

/** Stoppable = a worker is (or is about to be) running. */
export function isStoppable(status: TaskStatus): boolean {
  return status === "RUNNING" || status === "PENDING";
}
