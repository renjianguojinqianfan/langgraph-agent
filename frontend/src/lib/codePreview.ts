/** Languages the preview can colour. `plaintext` is the deliberate fallback for
 * unknown extensions: no grammar is fetched and no colour is invented. */
export type CodeLanguage =
  | "typescript"
  | "tsx"
  | "javascript"
  | "jsx"
  | "json"
  | "markdown"
  | "python"
  | "shellscript"
  | "css"
  | "html"
  | "yaml"
  | "toml"
  | "plaintext";

const EXTENSION_LANGUAGES: Record<string, CodeLanguage> = {
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  tsx: "tsx",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  json: "json",
  md: "markdown",
  markdown: "markdown",
  py: "python",
  sh: "shellscript",
  bash: "shellscript",
  zsh: "shellscript",
  css: "css",
  html: "html",
  htm: "html",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
};

/** Rendering cap; the backend already cuts the payload at 200 KB (#344). */
export const MAX_RENDER_LINES = 2000;

export interface PreviewLines {
  /** At most `MAX_RENDER_LINES`; what the gutter and code column render. */
  lines: string[];
  /** Lines after CRLF normalization, before the cap. */
  totalLines: number;
  /** `totalLines - lines.length`; non-zero drives the truncation notice. */
  hiddenLines: number;
}

/** Normalize line endings and split into editor lines. A final newline ends a
 * line instead of opening an extra blank one (VS Code shows one line for
 * `"a\n"`), while a file of only `"\n"` is exactly one empty line. */
export function splitPreviewLines(content: string): string[] {
  const normalized = content.replace(/\r\n?/g, "\n");
  if (normalized === "") return [];
  const lines = normalized.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

export function previewLines(content: string, maxLines = MAX_RENDER_LINES): PreviewLines {
  const all = splitPreviewLines(content);
  const lines = all.length > maxLines ? all.slice(0, maxLines) : all;
  return {
    lines,
    totalLines: all.length,
    hiddenLines: all.length - lines.length,
  };
}

/** What the preview says when the payload or the render cap omitted content.
 * One sentence when both apply: two stacked "download" lines read as noise at
 * 420px. */
export function previewNotices(truncated: boolean, hiddenLines: number): string[] {
  if (truncated && hiddenLines > 0) {
    return [`仅显示前 ${MAX_RENDER_LINES} 行（文件超过 200KB），完整内容请下载。`];
  }
  if (hiddenLines > 0) {
    return [`仅显示前 ${MAX_RENDER_LINES} 行，完整内容请下载。`];
  }
  if (truncated) {
    return ["文件超过 200KB，内容已截断；完整内容请下载。"];
  }
  return [];
}

/** 1-based gutter model, kept separate from the code lines so the numbers can
 * live in their own aria-hidden column and never enter a copy selection. */
export function lineNumbers(count: number): number[] {
  return Array.from({ length: Math.max(0, count) }, (_, index) => index + 1);
}

/** Digits needed by the widest rendered number; two characters minimum so the
 * gutter has breathing room at 1-9 lines. */
export function gutterDigits(lineCount: number): number {
  return Math.max(2, String(Math.max(0, lineCount)).length);
}

export function emptyFileCopy(): { title: string; hint: string } {
  return {
    title: "空文件",
    hint: "此文件没有内容；下载入口在页签顶部。",
  };
}

export interface Breadcrumb {
  /** Directory segments, oldest first; a leading "…" replaces deep ancestors. */
  dirs: string[];
  name: string;
}

/** Path -> breadcrumb. Deep paths keep the two nearest directories rather than
 * letting a 420px header overflow; the full path stays in the header `title`. */
export function breadcrumbForPath(path: string, maxDirs = 2): Breadcrumb {
  const segments = path
    .trim()
    .split(/[\\/]+/)
    .filter((segment) => segment.length > 0);
  const name = segments.pop() ?? "";
  const dirs =
    segments.length > maxDirs ? ["…", ...segments.slice(-maxDirs)] : segments;
  return { dirs, name };
}

/** Extension -> grammar. The last dot decides; a dot at index 0 is a dotfile,
 * not an extension (`.env` stays plaintext). */
export function languageForPath(path: string): CodeLanguage {
  const name = path.split(/[\\/]+/).pop() ?? path;
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot === name.length - 1) return "plaintext";
  return EXTENSION_LANGUAGES[name.slice(dot + 1).toLowerCase()] ?? "plaintext";
}
