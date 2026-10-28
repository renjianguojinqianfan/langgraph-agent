/** The directory the page was opened for (`?dir=`, set by the launcher). */
export function currentDir(): string | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get("dir");
  return raw && raw.trim() ? raw : null;
}

/**
 * Replace the `dir` query parameter in `search` (#242).
 *
 * Pure so the node-only vitest environment can pin the URL shape: an empty or
 * blank directory removes the parameter.
 */
export function withDirParam(search: string, dir: string | null): string {
  const params = new URLSearchParams(search);
  if (dir && dir.trim()) params.set("dir", dir);
  else params.delete("dir");
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** Keep `?dir=` shareable: the switch updates the URL, not a hidden store. */
export function setDirParam(dir: string): void {
  const { pathname, search, hash } = window.location;
  window.history.replaceState(
    null,
    "",
    `${pathname}${withDirParam(search, dir)}${hash}`
  );
}

