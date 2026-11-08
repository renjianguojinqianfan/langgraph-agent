import { describe, expect, it } from "vitest";
import type { ModelEntry, Provider } from "../types";
import {
  draftFromEntry,
  modelParamPatch,
  paramInheritHints,
  type ModelParamDraft,
} from "./modelParams";

// #267 / D-36: the parameter form's only mechanical surface (no jsdom, D-23).

const entry = (overrides: Partial<ModelEntry> = {}): ModelEntry => ({
  id: "e1",
  provider_id: "p1",
  model: "m1",
  name: "Model 1",
  ...overrides,
});

const provider = (overrides: Partial<Provider> = {}): Provider => ({
  id: "p1",
  name: "Provider 1",
  protocol: "openai-compatible",
  base_url: "https://api.example.com/v1",
  api_key_env: "LLM_API_KEY",
  preset: false,
  key_set: false,
  key_source: "none",
  ...overrides,
});

const draft = (overrides: Partial<ModelParamDraft> = {}): ModelParamDraft => ({
  temperature: "",
  max_tokens: "",
  thinking: "inherit",
  reasoning_effort: "inherit",
  ...overrides,
});

describe("draftFromEntry (raw entry overrides → form strings)", () => {
  it("maps unset params to empty / inherit", () => {
    expect(draftFromEntry(entry({ temperature: null, max_tokens: null, thinking: null, reasoning_effort: null }))).toEqual({
      temperature: "",
      max_tokens: "",
      thinking: "inherit",
      reasoning_effort: "inherit",
    });
  });

  it("maps set params, including thinking true/false and effort", () => {
    expect(
      draftFromEntry(
        entry({ temperature: 0.5, max_tokens: 4096, thinking: true, reasoning_effort: "high" })
      )
    ).toEqual({
      temperature: "0.5",
      max_tokens: "4096",
      thinking: "on",
      reasoning_effort: "high",
    });
    expect(draftFromEntry(entry({ thinking: false })).thinking).toBe("off");
  });
});

describe("modelParamPatch (omitted = unchanged, explicit null = clear)", () => {
  it("emits no body when nothing changed", () => {
    const original = entry({ temperature: 0.3, max_tokens: 1024, thinking: true, reasoning_effort: "low" });
    const result = modelParamPatch(original, draftFromEntry(original), "openai-compatible");
    expect(result.errors).toEqual({});
    expect(result.changed).toBe(false);
    expect(result.patch).toEqual({});
  });

  it("clears a numeric override with null when the input is emptied", () => {
    const result = modelParamPatch(entry({ temperature: 0.7 }), draft({ temperature: "  " }), "openai-compatible");
    expect(result.errors).toEqual({});
    expect(result.patch).toEqual({ temperature: null });
    expect(result.changed).toBe(true);
  });

  it("clears a max_tokens override with null", () => {
    const result = modelParamPatch(entry({ max_tokens: 2048 }), draft({ max_tokens: "" }), "openai-compatible");
    expect(result.errors).toEqual({});
    expect(result.patch).toEqual({ max_tokens: null });
  });

  it("only carries the fields that actually changed", () => {
    const original = entry({ temperature: 0.2, max_tokens: 2048 });
    const result = modelParamPatch(original, draft({ temperature: "0.9", max_tokens: "2048" }), "openai-compatible");
    expect(result.patch).toEqual({ temperature: 0.9 });
  });

  it("maps the thinking tri-state to true / false / null", () => {
    expect(modelParamPatch(entry({}), draft({ thinking: "on" }), "openai-compatible").patch).toEqual({ thinking: true });
    expect(modelParamPatch(entry({ thinking: true }), draft({ thinking: "off" }), "openai-compatible").patch).toEqual({ thinking: false });
    expect(modelParamPatch(entry({ thinking: true }), draft({ thinking: "inherit" }), "openai-compatible").patch).toEqual({ thinking: null });
    // Already inherited → no write.
    expect(modelParamPatch(entry({ thinking: null }), draft({ thinking: "inherit" }), "openai-compatible").changed).toBe(false);
  });

  it("maps reasoning_effort and can clear it to inherit", () => {
    expect(
      modelParamPatch(entry({}), draft({ reasoning_effort: "medium" }), "openai-compatible").patch
    ).toEqual({ reasoning_effort: "medium" });
    expect(
      modelParamPatch(entry({ reasoning_effort: "high" }), draft({ reasoning_effort: "inherit" }), "openai-compatible").patch
    ).toEqual({ reasoning_effort: null });
  });

  it("rejects non-numeric / out-of-range temperature per protocol", () => {
    const openai = "openai-compatible";
    expect(modelParamPatch(entry({}), draft({ temperature: "abc" }), openai).errors.temperature).toContain("数字");
    expect(modelParamPatch(entry({}), draft({ temperature: "-0.1" }), openai).errors.temperature).toContain("0–2");
    expect(modelParamPatch(entry({}), draft({ temperature: "2.5" }), openai).errors.temperature).toContain("0–2");
    // Anthropic's Messages range is 0–1 (backend-mirrored, not a silent block).
    expect(modelParamPatch(entry({}), draft({ temperature: "1.5" }), "anthropic").errors.temperature).toContain("0–1");
    expect(modelParamPatch(entry({}), draft({ temperature: "1.5" }), openai).errors.temperature).toBeUndefined();
    expect(modelParamPatch(entry({}), draft({ temperature: "0.5" }), "anthropic").patch).toEqual({ temperature: 0.5 });
  });

  it("rejects max_tokens that is not a positive integer", () => {
    for (const bad of ["0", "-1", "1.5", "abc"]) {
      expect(modelParamPatch(entry({}), draft({ max_tokens: bad }), "openai-compatible").errors.max_tokens).toContain("正整数");
    }
    expect(modelParamPatch(entry({}), draft({ max_tokens: "4096" }), "openai-compatible").patch).toEqual({
      max_tokens: 4096,
    });
  });

  it("does not silently intercept the Anthropic thinking / effort combination (service 400 is the gate)", () => {
    const thinking = modelParamPatch(entry({}), draft({ thinking: "on" }), "anthropic");
    expect(thinking.errors).toEqual({});
    expect(thinking.patch).toEqual({ thinking: true });
    const effort = modelParamPatch(entry({}), draft({ reasoning_effort: "high" }), "anthropic");
    expect(effort.errors).toEqual({});
    expect(effort.patch).toEqual({ reasoning_effort: "high" });
  });
});

describe("paramInheritHints (entry override vs provider default)", () => {
  it("names the built-in defaults when neither level sets a value", () => {
    const hints = paramInheritHints(provider({}), "openai-compatible");
    expect(hints.temperature).toContain("0.2");
    expect(hints.max_tokens).toContain("内置默认");
    expect(hints.thinking).toContain("未设置");
    expect(hints.reasoning_effort).toContain("未设置");
    expect(paramInheritHints(provider({ protocol: "anthropic" }), "anthropic").max_tokens).toContain("4096");
  });

  it("shows the provider default that an unset entry field inherits", () => {
    const hints = paramInheritHints(
      provider({ temperature: 0.6, max_tokens: 2048, thinking: false, reasoning_effort: "low" }),
      "openai-compatible"
    );
    // #300 L-3: the hint says what *clearing* inherits, so an entry that
    // currently overrides the value is not misread as "already inherited".
    expect(hints.temperature).toBe("清空后继承 Provider 默认 0.6");
    expect(hints.max_tokens).toBe("清空后继承 Provider 默认 2048");
    expect(hints.thinking).toContain("清空后继承");
    expect(hints.thinking).toContain("关");
    expect(hints.reasoning_effort).toContain("清空后继承");
    expect(hints.reasoning_effort).toContain("low");
  });
});
