/**
 * #342: `/tasks` requests the left task rail. `TaskView` owns the drawer state
 * and renders the rail statically on wide screens, so a DOM event is the
 * smallest wire between the composer and that owner (no store slice, no prop
 * drilling through `TaskPanel`).
 */
export const OPEN_TASK_RAIL_EVENT = "qianfan:open-rail";

export function requestTaskRail(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(OPEN_TASK_RAIL_EVENT));
}
