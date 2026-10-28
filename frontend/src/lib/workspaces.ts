/** Recent workspace paths for the switcher (#242); storage failures are non-fatal. */
const RECENT_KEY = "qianfan_recent_workspaces";
const MAX_RECENT = 8;

/** Newest first, deduped, capped; blank input is ignored (pure, testable). */
export function addRecentWorkspace(
  recents: string[],
  dir: string,
  limit = MAX_RECENT
): string[] {
  const trimmed = dir.trim();
  if (!trimmed) return recents;
  return [trimmed, ...recents.filter((path) => path !== trimmed)].slice(0, limit);
}

type StorageLike = Pick<Storage, "getItem" | "setItem">;

function browserStorage(): StorageLike | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readRecentWorkspaces(
  storage: StorageLike | null = browserStorage()
): string[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(RECENT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    return [];
  }
}

/** Persist the newest path and return the updated list (never throws). */
export function rememberWorkspace(
  dir: string,
  storage: StorageLike | null = browserStorage()
): string[] {
  const next = addRecentWorkspace(readRecentWorkspaces(storage), dir);
  try {
    storage?.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* storage may be unavailable/full; the switch itself still works */
  }
  return next;
}
