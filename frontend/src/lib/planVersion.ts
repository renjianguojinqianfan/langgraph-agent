/** #256 plan-version merge: a saved/loaded version only ever grows, and the
 * empty-plan read (version 0) must never walk a real version back. Kept in
 * its own module so the request-dedup helpers stay one concern (PR #294 LOW). */
export function nextPlanVersion(current: number, incoming: number): number {
  return Math.max(current, incoming);
}

/** Should the plan pane refetch because the store's version moved ahead of the
 * one this pane last synced? `>` only: equal versions are already in sync, and
 * a smaller stored version is stale, not a reason to reload (#258 终稿). */
export function shouldReloadOnPlanVersion(synced: number, current: number): boolean {
  return current > synced;
}
