import type {
  ChangeDetail,
  ChangeFileEntry,
  DiffHunk,
  DiffRow,
  SplitRow,
} from "../types";

/** The card shows this many rows before the "expand" control. */
export const CHANGE_ROW_COLLAPSE = 4;

export type DiffMode = "unified" | "split";

export interface ChangeCardModel {
  header: string;
  rows: ChangeFileEntry[];
  hidden: number;
  hasMore: boolean;
}

function displayOf(file: ChangeFileEntry): string {
  return file.display || file.path;
}

/**
 * The round-tail card model. `total` is the API's full count: when the list
 * itself is truncated (maxFiles), the header still says how many files the
 * round touched.
 */
export function changeCard(
  files: readonly ChangeFileEntry[],
  total: number,
  expanded: boolean
): ChangeCardModel {
  const rows = visibleChangeFiles(files, expanded);
  const hidden = hiddenChangeCount(files, expanded);
  return {
    header: changeHeader(files, total),
    rows,
    hidden,
    hasMore: hidden > 0,
  };
}

export function changeHeader(files: readonly ChangeFileEntry[], total: number): string {
  if (total === 1 && files[0]) return `已编辑 ${displayOf(files[0])}`;
  return `已编辑 ${total} 个文件`;
}

export function visibleChangeFiles(
  files: readonly ChangeFileEntry[],
  expanded: boolean
): ChangeFileEntry[] {
  return expanded ? [...files] : files.slice(0, CHANGE_ROW_COLLAPSE);
}

export function hiddenChangeCount(
  files: readonly ChangeFileEntry[],
  expanded: boolean
): number {
  return expanded ? 0 : Math.max(0, files.length - CHANGE_ROW_COLLAPSE);
}

export function changeStatusLabel(file: ChangeFileEntry): string {
  if (file.binary) return "二进制文件";
  if (file.oversized) return "文件过大";
  if (file.status === "added") return "新增";
  if (file.status === "deleted") return "已删除";
  return "已修改";
}

/** Line-count copy; zero sides are omitted rather than printed as `+0`. */
export function changeCountsText(added: number, deleted: number): string {
  if (added > 0 && deleted > 0) return `+${added} −${deleted}`;
  if (added > 0) return `+${added}`;
  if (deleted > 0) return `−${deleted}`;
  return "无行数变化";
}

/** A single-sided change cannot be shown side by side. */
export function shouldForceUnified(file: Pick<ChangeFileEntry, "status">): boolean {
  return file.status === "added" || file.status === "deleted";
}

export function diffMode(
  requested: DiffMode,
  file: Pick<ChangeFileEntry, "status">
): DiffMode {
  return shouldForceUnified(file) ? "unified" : requested;
}

function rowFromLine(
  line: string,
  oldLine: number,
  newLine: number
): { row: DiffRow; oldNext: number; newNext: number } {
  const kind = line[0];
  const text = line.slice(1);
  if (kind === "-") {
    return {
      row: { kind: "delete", oldLine, newLine: null, text },
      oldNext: oldLine + 1,
      newNext: newLine,
    };
  }
  if (kind === "+") {
    return {
      row: { kind: "add", oldLine: null, newLine, text },
      oldNext: oldLine,
      newNext: newLine + 1,
    };
  }
  return {
    row: { kind: "context", oldLine, newLine, text },
    oldNext: oldLine + 1,
    newNext: newLine + 1,
  };
}

/** Flatten hunks into rows that carry both sides' line numbers. */
export function flattenHunks(hunks: readonly DiffHunk[]): DiffRow[] {
  const rows: DiffRow[] = [];
  for (const hunk of hunks) {
    let oldLine = hunk.oldStart;
    let newLine = hunk.newStart;
    for (const line of hunk.lines) {
      if (!line) continue;
      const next = rowFromLine(line, oldLine, newLine);
      rows.push(next.row);
      oldLine = next.oldNext;
      newLine = next.newNext;
    }
  }
  return rows;
}

/** The flattened row kind is the semantic tone; components own the classes. */
export type DiffKind = DiffRow["kind"];

/** The unified-view marker (the split view has no marker column). */
export function diffPrefix(kind: DiffKind): string {
  switch (kind) {
    case "add":
      return "+";
    case "delete":
      return "-";
    default:
      return " ";
  }
}

/**
 * Monotonic guard for the card's delayed hover preview. Each hover takes a
 * generation, and a response may only land while that generation is still the
 * newest — a pointer/focus leave (or unmount) invalidates the in-flight one.
 */
export interface PreviewGate {
  next: () => number;
  invalidate: () => void;
  isCurrent: (generation: number) => boolean;
}

export function createPreviewGate(): PreviewGate {
  let generation = 0;
  return {
    next: () => {
      generation += 1;
      return generation;
    },
    invalidate: () => {
      generation += 1;
    },
    isCurrent: (value) => value === generation,
  };
}

/**
 * Pair deletion and addition runs index-wise for the split view. Context rows
 * keep both sides; a longer side leaves the other cell blank.
 */
export function splitRows(rows: readonly DiffRow[]): SplitRow[] {
  const split: SplitRow[] = [];
  let i = 0;
  while (i < rows.length) {
    const row = rows[i];
    if (row.kind === "context") {
      split.push({
        kind: "context",
        oldLine: row.oldLine,
        newLine: row.newLine,
        oldText: row.text,
        newText: row.text,
      });
      i += 1;
      continue;
    }
    const deletes: DiffRow[] = [];
    const adds: DiffRow[] = [];
    while (i < rows.length && rows[i].kind !== "context") {
      if (rows[i].kind === "delete") deletes.push(rows[i]);
      else adds.push(rows[i]);
      i += 1;
    }
    const length = Math.max(deletes.length, adds.length);
    for (let j = 0; j < length; j += 1) {
      const del = deletes[j];
      const add = adds[j];
      let kind: SplitRow["kind"];
      if (del && add) {
        kind = "change";
      } else if (del) {
        kind = "delete";
      } else {
        kind = "add";
      }
      split.push({
        kind,
        oldLine: del?.oldLine ?? null,
        newLine: add?.newLine ?? null,
        oldText: del?.text ?? null,
        newText: add?.text ?? null,
      });
    }
  }
  return split;
}

export interface ChangesFailureCopy {
  title: string;
  hint: string;
}

/** Status-preserving failure copy; never echoes server detail / paths. */
export function changesFailure(status: number, _detail = ""): ChangesFailureCopy {
  if (status === 0) {
    return {
      title: "无法读取变更",
      hint: "后台没有响应；确认服务在运行后重试。",
    };
  }
  if (status === 401) {
    return {
      title: "登录已失效",
      hint: "重新登录后再打开这一轮变更。",
    };
  }
  if (status === 404) {
    return {
      title: "变更记录不存在",
      hint: "记录可能已随任务清理；回到对话刷新后再试。",
    };
  }
  if (status === 400) {
    return {
      title: "变更加载请求无效",
      hint: "轮次或文件索引不合法；刷新后再试。",
    };
  }
  if (status === 409) {
    return {
      title: "变更暂不可用",
      hint: "任务状态已变化；刷新后再试。",
    };
  }
  return {
    title: "变更加载失败",
    hint: "稍后重试；若持续失败，查看后台日志。",
  };
}

/** In-memory reviewed identity: one marker set per (task, round). */
export function reviewKey(taskId: string, round: number): string {
  return `${taskId}:${round}`;
}

export function toggleReviewed(
  reviewed: readonly string[],
  path: string
): string[] {
  return reviewed.includes(path)
    ? reviewed.filter((item) => item !== path)
    : [...reviewed, path];
}

export function reviewedCount(
  files: readonly Pick<ChangeFileEntry, "path">[],
  reviewed: readonly string[]
): number {
  return files.filter((file) => reviewed.includes(file.path)).length;
}

export function reviewedText(count: number, total: number): string {
  return `${count}/${total} reviewed`;
}

/** True when a detail response can render a diff body at all. */
export function hasDiffBody(detail: ChangeDetail | null): boolean {
  return !!detail && !detail.binary && !detail.oversized && detail.hunks.length > 0;
}
