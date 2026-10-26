import { describe, expect, it } from "vitest";
import { formatBytes, relativeTime } from "./format";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

// Local wall-clock noon keeps the date fallback deterministic in every timezone.
const NOW = new Date(2026, 9, 27, 12, 0, 0).getTime();

function before(offset: number): string {
  return new Date(NOW - offset).toISOString();
}

describe("relativeTime", () => {
  it("returns a dash for empty or invalid timestamps", () => {
    expect(relativeTime("", NOW)).toBe("—");
    expect(relativeTime("   ", NOW)).toBe("—");
    expect(relativeTime("not-a-date", NOW)).toBe("—");
  });

  it("shows 刚刚 below a minute, including future clock skew", () => {
    expect(relativeTime(before(0), NOW)).toBe("刚刚");
    expect(relativeTime(before(MINUTE - 1), NOW)).toBe("刚刚");
    expect(relativeTime(before(-5_000), NOW)).toBe("刚刚");
  });

  it("counts minutes", () => {
    expect(relativeTime(before(MINUTE), NOW)).toBe("1 分钟前");
    expect(relativeTime(before(59 * MINUTE + 59_000), NOW)).toBe("59 分钟前");
  });

  it("counts hours", () => {
    expect(relativeTime(before(HOUR), NOW)).toBe("1 小时前");
    expect(relativeTime(before(23 * HOUR + 59 * MINUTE), NOW)).toBe("23 小时前");
  });

  it("counts days up to a week", () => {
    expect(relativeTime(before(DAY), NOW)).toBe("1 天前");
    expect(relativeTime(before(6 * DAY + 23 * HOUR), NOW)).toBe("6 天前");
  });

  it("falls back to the local date from seven days on", () => {
    expect(relativeTime(before(7 * DAY), NOW)).toBe("2026-10-20");
    expect(relativeTime(before(30 * DAY), NOW)).toBe("2026-09-27");
  });
});

describe("formatBytes", () => {
  it("marks invalid sizes", () => {
    expect(formatBytes(-1)).toBe("—");
    expect(formatBytes(Number.NaN)).toBe("—");
  });

  it("switches units at 1024", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(2 * 1024 * 1024)).toBe("2.0 MB");
  });
});
