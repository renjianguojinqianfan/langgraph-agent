import { describe, expect, it } from "vitest";
import { previewFailure } from "./preview";

describe("previewFailure", () => {
  it("names the binary refusal and points at download", () => {
    const copy = previewFailure(415);
    expect(copy.title).toContain("二进制");
    expect(copy.hint).toContain("下载");
  });

  it("separates an escaped path from a missing file", () => {
    const escape = previewFailure(400);
    const missing = previewFailure(404);
    expect(escape.title).not.toBe(missing.title);
    expect(escape.title).toContain("工作区");
    expect(missing.title).toContain("不存在");
  });

  it("names a gone workspace with a next step", () => {
    const copy = previewFailure(409);
    expect(copy.title).toContain("工作区");
    expect(copy.hint).toContain("切回存在的目录");
  });

  it("separates a deleted task from a deleted file", () => {
    expect(previewFailure(404, "task not found").title).toBe("任务不存在");
    expect(previewFailure(404, "file not found").title).toBe("文件不存在");
  });

  it("treats a malformed 200 as a parse failure, not a success", () => {
    expect(previewFailure(200).title).toContain("异常");
  });

  it("distinguishes a network failure from an HTTP one", () => {
    expect(previewFailure(0).title).toBe("无法连接后台");
    expect(previewFailure(500, "boom").title).toBe("预览失败（HTTP 500）");
  });

  it("points an escaped-path failure at the path shown in the tab header", () => {
    expect(previewFailure(400).hint).toContain("核对上方路径");
  });

  it("says a stale login also blocks the download link", () => {
    expect(previewFailure(401).hint).toContain("下载");
  });

  it("keeps the server detail for unclassified failures", () => {
    expect(previewFailure(500, "boom").hint).toBe("boom");
    expect(previewFailure(500, "").hint).toContain("重试");
  });
});
