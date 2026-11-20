import { describe, expect, it } from "vitest";
import type { SkillInfo } from "../types";
import { BACKEND_UNREACHABLE } from "./copy";
import {
  dedupeSkills,
  filterSkills,
  insertSkillReference,
  skillCatalogFailure,
  skillGroupView,
  skillReference,
  type SkillCatalogSnapshot,
} from "./skillCatalog";
import { slashTokenAt } from "./slashCommands";

const skill = (
  name: string,
  description: string,
  layer: SkillInfo["layer"]
): SkillInfo => ({ name, description, layer });

const ready = (
  over: Partial<SkillCatalogSnapshot> = {}
): SkillCatalogSnapshot => ({
  phase: "ready",
  enabled: true,
  skills: [],
  error: "",
  ...over,
});

describe("dedupeSkills / #345 same-name skills", () => {
  it("keeps discovery order for distinct names", () => {
    const items = dedupeSkills([
      skill("alpha", "first", "home"),
      skill("beta", "second", "workspace"),
    ]);
    expect(items.map((item) => item.name)).toEqual(["alpha", "beta"]);
    expect(items.map((item) => item.layerLabel)).toEqual(["home", "workspace"]);
  });

  it("merges same-name home + workspace entries into one row", () => {
    const items = dedupeSkills([
      skill("shared", "home copy", "home"),
      skill("other", "other", "workspace"),
      skill("shared", "workspace copy", "workspace"),
    ]);

    expect(items).toHaveLength(2);
    const shared = items.find((item) => item.name === "shared");
    expect(shared?.layers).toEqual(["home", "workspace"]);
    expect(shared?.layerLabel).toBe("home + workspace");
    // The first non-empty description wins; the row never implies one layer.
    expect(shared?.description).toBe("home copy");
  });

  it("falls back to the later description when the first layer has none", () => {
    const items = dedupeSkills([
      skill("shared", "", "home"),
      skill("shared", "workspace copy", "workspace"),
    ]);
    expect(items[0].description).toBe("workspace copy");
  });

  it("uses the first non-empty description when the same name repeats", () => {
    const items = dedupeSkills([
      skill("shared", "", "home"),
      skill("shared", "later", "workspace"),
      skill("shared", "third", "workspace"),
    ]);
    expect(items[0].description).toBe("later");
    expect(items[0].layers).toEqual(["home", "workspace"]);
  });
});

describe("filterSkills / #345 name and description filter", () => {
  const items = dedupeSkills([
    skill("pdf", "Read and split PDFs", "home"),
    skill("review", "Code review checklist", "workspace"),
  ]);

  it("returns everything for an empty query", () => {
    expect(filterSkills(items, "").map((item) => item.name)).toEqual([
      "pdf",
      "review",
    ]);
    expect(filterSkills(items, "   ").map((item) => item.name)).toEqual([
      "pdf",
      "review",
    ]);
  });

  it("matches name or description, case-insensitively", () => {
    expect(filterSkills(items, "pd").map((item) => item.name)).toEqual(["pdf"]);
    expect(filterSkills(items, "SPLIT").map((item) => item.name)).toEqual([
      "pdf",
    ]);
    expect(filterSkills(items, "CHECKLIST").map((item) => item.name)).toEqual([
      "review",
    ]);
  });

  it("returns no rows for a miss", () => {
    expect(filterSkills(items, "zzz")).toEqual([]);
  });
});

describe("skillGroupView / #345 menu states", () => {
  it("is hidden while loading when the query filters commands", () => {
    const snapshot: SkillCatalogSnapshot = {
      phase: "loading",
      enabled: true,
      skills: [],
      error: "",
    };
    const view = skillGroupView(snapshot, "new");
    expect(view.status).toBe("hidden");
    expect(view.items).toEqual([]);
    expect(view.canRetry).toBe(false);
  });

  it("shows a loading note on a bare slash", () => {
    const view = skillGroupView(
      { phase: "loading", enabled: true, skills: [], error: "" },
      ""
    );
    expect(view.status).toBe("loading");
    expect(view.note).toBe("正在加载技能…");
  });

  it("shows a clear empty state when the skills package is off", () => {
    const disabled = ready({ enabled: false });
    const bare = skillGroupView(disabled, "");
    expect(bare.status).toBe("disabled");
    expect(bare.items).toEqual([]);
    expect(bare.note).toContain("技能能力包已关闭");
    expect(skillGroupView(disabled, "new").status).toBe("hidden");
  });

  it("shows an empty state when the package is on but no skill exists", () => {
    const view = skillGroupView(ready(), "");
    expect(view.status).toBe("empty");
    expect(view.note).toContain("没有可用技能");
    expect(skillGroupView(ready(), "new").status).toBe("hidden");
  });

  it("returns filtered rows and reports a filtered miss", () => {
    const items = [skill("pdf", "PDF tools", "home")];
    const hit = skillGroupView(ready({ skills: items }), "pd");
    expect(hit.status).toBe("ready");
    expect(hit.items.map((item) => item.name)).toEqual(["pdf"]);

    const miss = skillGroupView(ready({ skills: items }), "zzz");
    expect(miss.status).toBe("no-match");
    expect(miss.items).toEqual([]);
    expect(miss.note).toBe("没有匹配的技能");
  });

  it("keeps a catalog failure inside the skill group and offers retry", () => {
    const view = skillGroupView(
      {
        phase: "error",
        enabled: false,
        skills: [],
        error: "技能目录读取失败：HTTP 500",
      },
      ""
    );
    expect(view.status).toBe("error");
    expect(view.items).toEqual([]);
    expect(view.note).toBe("技能目录读取失败：HTTP 500");
    expect(view.canRetry).toBe(true);
    // The error stays visible even while the user filters commands.
    expect(skillGroupView(
      {
        phase: "error",
        enabled: false,
        skills: [],
        error: "技能目录读取失败：HTTP 500",
      },
      "new"
    ).status).toBe("error");
  });
});

describe("skillCatalogFailure / #345 failure copy", () => {
  it("names a transport failure", () => {
    expect(skillCatalogFailure(0, "")).toBe(
      `技能目录读取失败：${BACKEND_UNREACHABLE}`
    );
  });

  it("keeps the server reason for 400 / 401 / 500", () => {
    expect(skillCatalogFailure(401, "invalid or expired token")).toBe(
      "技能目录读取失败：invalid or expired token"
    );
    expect(skillCatalogFailure(500, "")).toBe("技能目录读取失败：HTTP 500");
  });
});

describe("insertSkillReference / #345 insertion", () => {
  it("writes the name only, never a layer", () => {
    expect(skillReference("shared")).toBe("使用技能「shared」：");
    expect(skillReference("shared")).not.toContain("home");
  });

  it("replaces the slash token and keeps the text after it", () => {
    const text = "hello /pd world";
    const token = slashTokenAt(text, 9)!;
    const result = insertSkillReference(text, token, "pdf");
    expect(result.text).toBe("hello 使用技能「pdf」： world");
    expect(result.caret).toBe("hello 使用技能「pdf」：".length);
  });

  it("inserts at the start and keeps the trailing draft", () => {
    const text = "/pd 后面的话";
    const token = slashTokenAt(text, 3)!;
    const result = insertSkillReference(text, token, "pdf");
    expect(result.text).toBe("使用技能「pdf」： 后面的话");
    expect(result.text.slice(result.caret)).toBe(" 后面的话");
  });

  it("replaces a whole token even when the caret sits mid-token", () => {
    const text = "/pdfx";
    const token = slashTokenAt(text, 3)!;
    expect(token.query).toBe("pd");
    expect(insertSkillReference(text, token, "pdf").text).toBe(
      "使用技能「pdf」："
    );
  });
});
