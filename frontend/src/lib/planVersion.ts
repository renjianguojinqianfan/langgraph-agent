/** #256 plan-version merge: a saved/loaded version only ever grows, and the
 * empty-plan read (version 0) must never walk a real version back. Kept in
 * its own module so the request-dedup helpers stay one concern (PR #294 LOW). */
export function nextPlanVersion(current: number, incoming: number): number {
  return Math.max(current, incoming);
}
