/**
 * Sequence guard for async UI work that the user can cancel (#250).
 *
 * `begin()` marks the start of one request; `invalidate()` (dialog closed,
 * cancelled or reopened) makes every in-flight token stale. A response may
 * still resolve after that, but `isCurrent()` keeps it from being applied.
 */
export interface RequestGate {
  begin: () => number;
  isCurrent: (token: number) => boolean;
  invalidate: () => void;
}

export function createRequestGate(): RequestGate {
  let current = 0;
  return {
    begin: () => ++current,
    isCurrent: (token) => token === current,
    invalidate: () => {
      current += 1;
    },
  };
}
