import type { TaskStatus } from "../types";

/** Parked = the round ended and the next message continues the same thread. */
export function isParked(status: TaskStatus): boolean {
  return status === "INTERRUPTED" || status === "FAILED";
}

/** Stoppable = a worker is (or is about to be) running. */
export function isStoppable(status: TaskStatus): boolean {
  return status === "RUNNING" || status === "PENDING";
}

/**
 * #230: poll the workspace file list only while the pane is actually visible
 * and the task is still running. A closed drawer (narrow-screen `<1180px`)
 * must not keep issuing `GET /tasks/{id}/files` every 2s.
 */
export function shouldPollFiles(status: TaskStatus, visible: boolean): boolean {
  return visible && status === "RUNNING";
}
