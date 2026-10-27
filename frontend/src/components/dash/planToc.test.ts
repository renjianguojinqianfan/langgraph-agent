import { describe, expect, it } from "vitest";
import { parsePlanToc, slugify } from "./planToc";

describe("parsePlanToc", () => {
  it("只收 h2/h3，行号是 1 基", () => {
    const md = ["# 标题", "", "## 目标", "正文", "### 步骤", ""].join("\n");
    expect(parsePlanToc(md)).toEqual([
      { level: 2, text: "目标", slug: "目标", line: 3 },
      { level: 3, text: "步骤", slug: "步骤", line: 5 },
    ]);
  });

  it("围栏代码里的井号不算标题", () => {
    const md = ["## 真标题", "```bash", "## 假标题", "```", "~~~", "### 也假", "~~~"].join(
      "\n"
    );
    expect(parsePlanToc(md).map((t) => t.text)).toEqual(["真标题"]);
  });

  it("重复标题按出现顺序加后缀", () => {
    const md = ["## 步骤", "## 步骤", "## 步骤"].join("\n");
    expect(parsePlanToc(md).map((t) => t.slug)).toEqual([
      "步骤",
      "步骤-2",
      "步骤-3",
    ]);
  });

  it("内联标记从文字里去掉", () => {
    const md = "## **目标** 与 `code`";
    expect(parsePlanToc(md)[0]).toMatchObject({
      text: "目标 与 code",
      slug: "目标-与-code",
    });
  });

  it("尾部井号与空标题被清理", () => {
    const md = ["## 标题 ##", "##   "].join("\n");
    expect(parsePlanToc(md).map((t) => t.text)).toEqual(["标题"]);
  });
});

describe("slugify", () => {
  it("空白折叠为连字符，标点去掉", () => {
    expect(slugify("Hello, World!")).toBe("hello-world");
    expect(slugify("a  b")).toBe("a-b");
  });
});
