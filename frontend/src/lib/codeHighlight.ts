import type { HighlighterCore, ThemedToken } from "shiki";
import type { CodeLanguage } from "./codePreview";

/** One Shiki token. `color` is a CSS variable (see `--shiki-*` in index.css),
 * so highlighting follows the app palette instead of a baked-in theme. */
export type HighlightToken = ThemedToken;
export type HighlightedLines = ThemedToken[][];

type HighlightLanguage = Exclude<CodeLanguage, "plaintext">;

/** Statically analysable dynamic imports: each grammar becomes its own chunk,
 * so the initial bundle only pays for the preview shell. `html` pulls its own
 * embedded CSS/JS grammars. */
const GRAMMARS: Record<HighlightLanguage, () => Promise<{ default: unknown }>> = {
  typescript: () => import("@shikijs/langs/typescript"),
  tsx: () => import("@shikijs/langs/tsx"),
  javascript: () => import("@shikijs/langs/javascript"),
  jsx: () => import("@shikijs/langs/jsx"),
  json: () => import("@shikijs/langs/json"),
  markdown: () => import("@shikijs/langs/markdown"),
  python: () => import("@shikijs/langs/python"),
  shellscript: () => import("@shikijs/langs/shellscript"),
  css: () => import("@shikijs/langs/css"),
  html: () => import("@shikijs/langs/html"),
  yaml: () => import("@shikijs/langs/yaml"),
  toml: () => import("@shikijs/langs/toml"),
};

const THEME_NAME = "css-variables";
const variablePrefix = "--shiki-";

let core: Promise<HighlighterCore> | null = null;
const loaded = new Set<HighlightLanguage>();
const loading = new Map<HighlightLanguage, Promise<void>>();

async function createCore(): Promise<HighlighterCore> {
  const [{ createHighlighterCore, createCssVariablesTheme }, { createJavaScriptRegexEngine }] =
    await Promise.all([import("shiki/core"), import("shiki/engine/javascript")]);
  return createHighlighterCore({
    themes: [
      createCssVariablesTheme({
        name: THEME_NAME,
        variablePrefix,
        variableDefaults: {},
        fontStyle: true,
      }),
    ],
    // The JS engine avoids shipping the ~600 KB Oniguruma wasm. Grammars that
    // need an unsupported construct degrade to a readable token stream instead
    // of failing the whole preview (`forgiving`).
    engine: createJavaScriptRegexEngine({ forgiving: true }),
  });
}

function highlighter(): Promise<HighlighterCore> {
  if (core === null) {
    core = createCore().catch((error: unknown) => {
      core = null;
      throw error;
    });
  }
  return core;
}

async function ensureLanguage(instance: HighlighterCore, language: HighlightLanguage): Promise<void> {
  if (loaded.has(language)) return;
  const inFlight = loading.get(language);
  if (inFlight) {
    await inFlight;
    return;
  }
  const task = (async () => {
    const { default: registrations } = await GRAMMARS[language]();
    // Shiki accepts the language module's default registration array directly.
    await instance.loadLanguage(registrations as never);
    loaded.add(language);
  })().finally(() => loading.delete(language));
  loading.set(language, task);
  await task;
}

/**
 * Lazily highlight the shown lines. Returns `null` when the language is plain
 * text or when loading/tokenizing fails; the caller then keeps the uncoloured
 * `<pre>` already on screen (a failed highlighter must never blank a preview).
 *
 * Per-line tokens are used intentionally: the gutter is rendered as its own
 * column and never receives token text.
 */
export async function highlightLines(
  language: CodeLanguage,
  lines: readonly string[]
): Promise<HighlightedLines | null> {
  if (language === "plaintext" || lines.length === 0) return null;
  try {
    const instance = await highlighter();
    await ensureLanguage(instance, language);
    return await instance.codeToTokensBase(lines.join("\n"), {
      lang: language,
      theme: THEME_NAME,
    });
  } catch {
    return null;
  }
}
