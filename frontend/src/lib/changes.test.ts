import { describe, expect, it } from "vitest";
import {
  CHANGE_ROW_COLLAPSE,
  changeCard,
  changeCountsText,
  changeHeader,
  changeStatusLabel,
  changesFailure,
  changesTruncatedText,
  CHANGES_MAX_FILES,
  createPreviewGate,
  diffMode,
  diffPrefix,
  flattenHunks,
  hiddenChangeCount,
  reviewKey,
  reviewedCount,
  reviewedText,
  shouldForceUnified,
  splitRows,
  toggleReviewed,
  visibleChangeFiles,
} from "./changes";
import type { ChangeDetail, ChangeFileEntry, DiffHunk } from "../types";

const file = (over: Partial<ChangeFileEntry> = {}): ChangeFileEntry => ({
  index: 0,
  path: "src/a.ts",
  display: "src/a.ts",
  status: "modified",
  added: 2,
  deleted: 1,
  binary: false,
  oversized: false,
  ...over,
});

describe("change card", () => {
  it("names a single file and counts several", () => {
    expect(changeHeader([file()], 1)).toBe("已编辑 src/a.ts");
    expect(changeHeader([file(), file({ index: 1, path: "b.ts" })], 2)).toBe(
      "已编辑 2 个文件"
    );
  });

  it("uses the full total when the API truncates the file list", () => {
    expect(changeHeader([file()], 12)).toBe("已编辑 12 个文件");
  });

  it("shows four rows by default, then all of them", () => {
    const files = Array.from({ length: 6 }, (_, i) =>
      file({ index: i, path: `f${i}.ts`, display: `f${i}.ts` })
    );
    const collapsed = changeCard(files, 6, false);
    expect(collapsed.rows).toHaveLength(CHANGE_ROW_COLLAPSE);
    expect(collapsed.hidden).toBe(2);
    expect(collapsed.hasMore).toBe(true);
    expect(collapsed.header).toBe("已编辑 6 个文件");
    expect(hiddenChangeCount(files, false)).toBe(2);
    expect(visibleChangeFiles(files, true)).toHaveLength(6);
    expect(changeCard(files, 6, true).hidden).toBe(0);
  });

  it("does not advertise a collapse control for four rows or fewer", () => {
    const files = Array.from({ length: 4 }, (_, i) => file({ index: i }));
    const model = changeCard(files, 4, false);
    expect(model.hasMore).toBe(false);
    expect(model.hidden).toBe(0);
  });
});

describe("status and line counts", () => {
  it("separates the status label from the counts", () => {
    expect(changeStatusLabel(file({ status: "added" }))).toBe("新增");
    expect(changeStatusLabel(file({ status: "deleted" }))).toBe("已删除");
    expect(changeStatusLabel(file())).toBe("已修改");
    expect(changeStatusLabel(file({ binary: true }))).toBe("二进制文件");
    expect(changeStatusLabel(file({ oversized: true }))).toBe("文件过大");
  });

  it("renders added and deleted counts without inventing a zero", () => {
    expect(changeCountsText(3, 1)).toBe("+3 −1");
    expect(changeCountsText(3, 0)).toBe("+3");
    expect(changeCountsText(0, 2)).toBe("−2");
    expect(changeCountsText(0, 0)).toBe("无行数变化");
  });
});

describe("unified/split decision", () => {
  it("forces unified for a single-sided change", () => {
    expect(shouldForceUnified(file({ status: "added" }))).toBe(true);
    expect(shouldForceUnified(file({ status: "deleted" }))).toBe(true);
    expect(shouldForceUnified(file())).toBe(false);
    expect(diffMode("split", file({ status: "added" }))).toBe("unified");
    expect(diffMode("split", file())).toBe("split");
    expect(diffMode("unified", file())).toBe("unified");
  });
});

describe("flattenHunks", () => {
  const hunks: DiffHunk[] = [
    {
      oldStart: 10,
      oldLines: 3,
      newStart: 10,
      newLines: 4,
      lines: ["-old", "+new", "+extra", " tail"],
    },
  ];

  it("numbers both sides and tracks deletions", () => {
    expect(flattenHunks(hunks)).toEqual([
      { kind: "delete", oldLine: 10, newLine: null, text: "old" },
      { kind: "add", oldLine: null, newLine: 10, text: "new" },
      { kind: "add", oldLine: null, newLine: 11, text: "extra" },
      { kind: "context", oldLine: 11, newLine: 12, text: "tail" },
    ]);
  });

  it("pairs delete/add runs into split rows and leaves gaps blank", () => {
    expect(splitRows(flattenHunks(hunks))).toEqual([
      { kind: "change", oldLine: 10, newLine: 10, oldText: "old", newText: "new" },
      { kind: "add", oldLine: null, newLine: 11, oldText: null, newText: "extra" },
      { kind: "context", oldLine: 11, newLine: 12, oldText: "tail", newText: "tail" },
    ]);
  });
});

describe("diff row semantics", () => {
  it("names the unified marker once for both diff surfaces", () => {
    expect(diffPrefix("add")).toBe("+");
    expect(diffPrefix("delete")).toBe("-");
    expect(diffPrefix("context")).toBe(" ");
  });
});

describe("preview gate", () => {
  it("lets the newest generation land and drops cancelled ones", () => {
    const gate = createPreviewGate();
    const first = gate.next();
    expect(gate.isCurrent(first)).toBe(true);

    gate.invalidate();
    expect(gate.isCurrent(first)).toBe(false);

    const second = gate.next();
    expect(gate.isCurrent(second)).toBe(true);
    expect(gate.isCurrent(first)).toBe(false);

    // A newer request supersedes an older one even without a cancel.
    const third = gate.next();
    expect(gate.isCurrent(second)).toBe(false);
    expect(gate.isCurrent(third)).toBe(true);
  });
});

describe("review markers", () => {
  it("toggles a path without mutating the input", () => {
    const before: string[] = [];
    const once = toggleReviewed(before, "src/a.ts");
    expect(once).toEqual(["src/a.ts"]);
    expect(before).toEqual([]);
    expect(toggleReviewed(once, "src/a.ts")).toEqual([]);
  });

  it("counts reviewed files and resets by task/round key", () => {
    const files = [file(), file({ index: 1, path: "b.ts" })];
    expect(reviewedCount(files, ["src/a.ts"])).toBe(1);
    expect(reviewedText(1, 2)).toBe("1/2 reviewed");
    expect(reviewKey("task-1", 1)).not.toBe(reviewKey("task-1", 2));
    expect(reviewKey("task-1", 1)).not.toBe(reviewKey("task-2", 1));
  });
});

describe("failure copy", () => {
  it("names transport, auth, missing and bad-request causes", () => {
    expect(changesFailure(0).title).toContain("无法读取");
    expect(changesFailure(401).title).toContain("登录");
    expect(changesFailure(404).title).toContain("不存在");
    expect(changesFailure(400).title).toContain("请求");
    expect(changesFailure(500).title).toContain("失败");
  });

  it("never echoes a server path or detail", () => {
    const copy = changesFailure(500);
    expect(Object.keys(copy).sort()).toEqual(["hint", "title"]);
  });
});

describe("truncated summary copy", () => {
  it("names the mirror cap and the full round total", () => {
    expect(changesTruncatedText(812)).toBe(
      `文件过多：只列出前 ${CHANGES_MAX_FILES} 个（共 812 个）。`
    );
  });
});

// Keep the type imported so a response-shape regression is visible here.
const _detailShape: ChangeDetail = {
  path: "a.ts",
  status: "modified",
  added: 1,
  deleted: 1,
  binary: false,
  oversized: false,
  coarse: false,
  truncated: false,
  hunks: [],
};
void _detailShape;
