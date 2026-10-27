const MINUTE = 60_000;

/** Last path segment; workspace paths arrive with either separator. A path
 * made only of separators keeps its trimmed form ("/" stays "/"). */
export function basename(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "";
  const stripped = trimmed.replace(/[\\/]+$/, "");
  if (!stripped) return trimmed;
  return stripped.split(/[\\/]+/).pop() ?? stripped;
}
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Relative timestamp for history rows; `now` is injected so the function stays pure. */
export function relativeTime(iso: string, now: number): string {
  if (!iso.trim()) return "—";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "—";

  const elapsed = now - then.getTime();
  if (elapsed < MINUTE) return "刚刚";
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)} 分钟前`;
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)} 小时前`;
  if (elapsed < 7 * DAY) return `${Math.floor(elapsed / DAY)} 天前`;
  return localDate(then);
}

/** Byte count for workspace file facts. */
export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size < 0) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function localDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
