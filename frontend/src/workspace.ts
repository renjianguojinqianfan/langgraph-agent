/** The directory the page was opened for (`?dir=`, set by the launcher). */
export function currentDir(): string | null {
  const raw = new URLSearchParams(window.location.search).get("dir");
  return raw && raw.trim() ? raw : null;
}
