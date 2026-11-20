import { describe, expect, it } from "vitest";
import {
  MAX_RENDER_LINES,
  breadcrumbForPath,
  emptyFileCopy,
  gutterDigits,
  languageForPath,
  lineNumbers,
  previewLines,
  previewNotices,
  splitPreviewLines,
} from "./codePreview";

describe("languageForPath", () => {
  it("maps every extension the preview promises to highlight", () => {
    expect(languageForPath("src/app.ts")).toBe("typescript");
    expect(languageForPath("src/App.tsx")).toBe("tsx");
    expect(languageForPath("src/app.js")).toBe("javascript");
    expect(languageForPath("src/App.jsx")).toBe("jsx");
    expect(languageForPath("package.json")).toBe("json");
    expect(languageForPath("README.md")).toBe("markdown");
    expect(languageForPath("backend/main.py")).toBe("python");
    expect(languageForPath("scripts/run.sh")).toBe("shellscript");
    expect(languageForPath("src/index.css")).toBe("css");
    expect(languageForPath("public/index.html")).toBe("html");
    expect(languageForPath(".github/workflows/ci.yml")).toBe("yaml");
    expect(languageForPath("pyproject.toml")).toBe("toml");
  });

  it("accepts the common aliases of those families", () => {
    expect(languageForPath("a.mts")).toBe("typescript");
    expect(languageForPath("a.cts")).toBe("typescript");
    expect(languageForPath("a.mjs")).toBe("javascript");
    expect(languageForPath("a.cjs")).toBe("javascript");
    expect(languageForPath("a.markdown")).toBe("markdown");
    expect(languageForPath("a.bash")).toBe("shellscript");
    expect(languageForPath("a.zsh")).toBe("shellscript");
    expect(languageForPath("a.htm")).toBe("html");
    expect(languageForPath("a.yaml")).toBe("yaml");
  });

  it("looks at the final extension, case-insensitively", () => {
    expect(languageForPath("src/App.test.TSX")).toBe("tsx");
    expect(languageForPath("README.MD")).toBe("markdown");
  });

  it("falls back to plaintext for unknown or missing extensions", () => {
    expect(languageForPath("LICENSE")).toBe("plaintext");
    expect(languageForPath("notes.txt")).toBe("plaintext");
    expect(languageForPath("archive.tar.gz")).toBe("plaintext");
    expect(languageForPath(".env")).toBe("plaintext");
    expect(languageForPath("trailing.")).toBe("plaintext");
  });

  it("handles both path separators", () => {
    expect(languageForPath("C:\\repo\\src\\a.ts")).toBe("typescript");
  });
});

describe("splitPreviewLines", () => {
  it("treats an empty file as zero lines, not one empty line", () => {
    expect(splitPreviewLines("")).toEqual([]);
  });

  it("does not invent an extra line for a final newline", () => {
    expect(splitPreviewLines("a\n")).toEqual(["a"]);
    expect(splitPreviewLines("a\n\n")).toEqual(["a", ""]);
    expect(splitPreviewLines("\n")).toEqual([""]);
  });

  it("normalizes CRLF and lone CR so no carriage return reaches a line", () => {
    expect(splitPreviewLines("a\r\nb\r\n")).toEqual(["a", "b"]);
    expect(splitPreviewLines("a\rb")).toEqual(["a", "b"]);
  });

  it("keeps tabs and trailing spaces untouched", () => {
    expect(splitPreviewLines("\tif (x) {  \n}")).toEqual(["\tif (x) {  ", "}"]);
  });
});

describe("previewLines", () => {
  it("caps rendering at 2000 lines and reports what was hidden", () => {
    const content = Array.from({ length: MAX_RENDER_LINES + 1 }, (_, i) => `line ${i + 1}`).join("\n");
    const preview = previewLines(content);
    expect(MAX_RENDER_LINES).toBe(2000);
    expect(preview.lines).toHaveLength(2000);
    expect(preview.lines[0]).toBe("line 1");
    expect(preview.lines[1999]).toBe("line 2000");
    expect(preview.totalLines).toBe(2001);
    expect(preview.hiddenLines).toBe(1);
  });

  it("leaves a file at the cap untouched", () => {
    const content = Array.from({ length: MAX_RENDER_LINES }, (_, i) => `line ${i + 1}`).join("\n");
    const preview = previewLines(content);
    expect(preview.lines).toHaveLength(MAX_RENDER_LINES);
    expect(preview.hiddenLines).toBe(0);
  });
});

describe("previewNotices", () => {
  it("stays quiet for a complete, short file", () => {
    expect(previewNotices(false, 0)).toEqual([]);
  });

  it("names the rendered cap and the download escape hatch", () => {
    const [notice] = previewNotices(false, 5);
    expect(notice).toBe("仅显示前 2000 行，完整内容请下载。");
  });

  it("keeps the backend's 200 KB cut even when every line fits", () => {
    const [notice] = previewNotices(true, 0);
    expect(notice).toContain("200KB");
    expect(notice).toContain("下载");
  });

  it("combines both limits into one sentence", () => {
    expect(previewNotices(true, 5)).toEqual([
      "仅显示前 2000 行（文件超过 200KB），完整内容请下载。",
    ]);
  });
});

describe("lineNumbers / gutterDigits", () => {
  it("numbers rendered lines from 1 without carrying code text", () => {
    expect(lineNumbers(0)).toEqual([]);
    expect(lineNumbers(3)).toEqual([1, 2, 3]);
  });

  it("sizes the gutter for the widest number, with a two-digit floor", () => {
    expect(gutterDigits(0)).toBe(2);
    expect(gutterDigits(9)).toBe(2);
    expect(gutterDigits(10)).toBe(2);
    expect(gutterDigits(100)).toBe(3);
    expect(gutterDigits(MAX_RENDER_LINES)).toBe(4);
  });
});

describe("emptyFileCopy", () => {
  it("says the file is empty and keeps the download actionable", () => {
    const copy = emptyFileCopy();
    expect(copy.title).toBe("空文件");
    expect(copy.hint).toContain("下载");
  });
});

describe("breadcrumbForPath", () => {
  it("splits directory segments from the filename", () => {
    expect(breadcrumbForPath("src/lib/a.ts")).toEqual({
      dirs: ["src", "lib"],
      name: "a.ts",
    });
  });

  it("handles a root file and both separators", () => {
    expect(breadcrumbForPath("a.ts")).toEqual({ dirs: [], name: "a.ts" });
    expect(breadcrumbForPath("src\\lib\\a.ts")).toEqual({
      dirs: ["src", "lib"],
      name: "a.ts",
    });
  });

  it("collapses deep paths from the left so 420px keeps the nearest context", () => {
    expect(breadcrumbForPath("a/b/c/d/e.ts")).toEqual({
      dirs: ["…", "c", "d"],
      name: "e.ts",
    });
    expect(breadcrumbForPath("a/b/c.ts")).toEqual({ dirs: ["a", "b"], name: "c.ts" });
  });
});
