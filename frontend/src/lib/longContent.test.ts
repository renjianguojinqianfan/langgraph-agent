import { describe, expect, it } from "vitest";
import { LONG_TEXT } from "./longContent";
// Vite `?raw` (typed by vite/client, no @types/node needed) lets a node-env
// test read component sources: the 4 faces must reference the one constant.
import markdownSource from "./markdown.tsx?raw";
import userBubbleSource from "../components/center/UserBubble.tsx?raw";
import failureBlockSource from "../components/center/FailureBlock.tsx?raw";
import toolRowSource from "../components/center/ToolRow.tsx?raw";
import taskViewSource from "../pages/TaskView.tsx?raw";

/** 判据（#258 AC1 / D-28）：4 个面都必须引用同一契约常量；删掉任一面上的
 * `LONG_TEXT` 引用 → 本测试红。没有 jsdom 也能量测「组件带断行契约类」，
 * 因为契约由单一常量定义、静态源码可核。 */
const FACES: ReadonlyArray<readonly [string, string]> = [
  ["lib/markdown.tsx", markdownSource],
  ["components/center/UserBubble.tsx", userBubbleSource],
  ["components/center/FailureBlock.tsx", failureBlockSource],
  ["components/center/ToolRow.tsx", toolRowSource],
];

describe("long-content contract (D-28 / #258 AC1)", () => {
  it("the one constant carries both token halves", () => {
    expect(LONG_TEXT).toContain("min-w-0");
    expect(LONG_TEXT).toContain("[overflow-wrap:anywhere]");
  });

  it.each(FACES)("%s imports/uses LONG_TEXT", (_name, source) => {
    expect(source).toContain("LONG_TEXT");
  });

  it("does not paper over the regression with global overflow-x hidden", () => {
    expect(taskViewSource).not.toContain("overflow-x-hidden");
  });
});