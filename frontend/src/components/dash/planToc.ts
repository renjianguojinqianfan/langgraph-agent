export interface TocItem {
  level: 2 | 3;
  text: string;
  slug: string;
  /** 1-based source line, used to pin ids onto rendered headings. */
  line: number;
}

function headingText(raw: string): string {
  return raw
    .replace(/\s+#+\s*$/, "")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`~]/g, "")
    .trim();
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/\s+/g, "-")
    .replace(/[^\p{L}\p{N}_-]+/gu, "")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

/**
 * ATX h2/h3 headings outside fenced code blocks. Slugs are unique across every
 * heading (h1 included) so they line up with the rendered document.
 */
export function parsePlanToc(markdown: string): TocItem[] {
  const items: TocItem[] = [];
  const used = new Map<string, number>();
  let fence: string | null = null;

  markdown.split(/\r?\n/).forEach((line, index) => {
    const trimmed = line.trim();
    const fenceMatch = trimmed.match(/^(`{3,}|~{3,})/);
    if (fenceMatch) {
      const marker = fenceMatch[1][0];
      if (fence === null) fence = marker;
      else if (fence === marker) fence = null;
      return;
    }
    if (fence !== null) return;

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (!heading) return;
    const level = heading[1].length;
    const text = headingText(heading[2]);
    if (!text) return;

    const base = slugify(text) || `section-${index + 1}`;
    const seen = (used.get(base) ?? 0) + 1;
    used.set(base, seen);
    if (level < 2) return;
    items.push({
      level: level as 2 | 3,
      text,
      slug: seen > 1 ? `${base}-${seen}` : base,
      line: index + 1,
    });
  });

  return items;
}
