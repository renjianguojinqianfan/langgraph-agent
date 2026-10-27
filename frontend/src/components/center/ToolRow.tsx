import { useState } from "react";
import { cx } from "../../lib/cx";
import type { ToolCallRecord } from "../../types";
import { Icon } from "../ui/Icon";

/** Only terminal states are result words; `pending` is still in flight (D6). */
const RESULT: Record<string, { label: string; tone: string }> = {
  pending: { label: "进行中", tone: "text-signal-now" },
  running: { label: "进行中", tone: "text-signal-now" },
  success: { label: "成功", tone: "text-state-ok" },
  failed: { label: "失败", tone: "text-state-danger" },
  skipped: { label: "未执行", tone: "text-tertiary" },
};

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function summarizeInput(input: Record<string, unknown>): string {
  try {
    const compact = JSON.stringify(input);
    return compact && compact !== "{}" ? compact : "";
  } catch {
    return "";
  }
}

function outputText(tc: ToolCallRecord): string {
  const out = tc.output;
  if (out == null) return "（尚无输出）";
  if (
    out &&
    typeof out === "object" &&
    tc.tool_name.startsWith("mcp__") &&
    typeof (out as { text?: unknown }).text === "string"
  ) {
    return (out as { text: string }).text;
  }
  return pretty(out);
}

export function ToolRow({ tc }: { tc: ToolCallRecord }) {
  const [open, setOpen] = useState(false);
  const result = RESULT[tc.status] ?? { label: tc.status, tone: "text-tertiary" };
  const summary = summarizeInput(tc.input);

  return (
    <div className="rounded-well bg-surface-well">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
      >
        <span
          className="min-w-0 shrink-0 max-w-[45%] truncate font-mono text-machine text-secondary"
          title={tc.tool_name}
        >
          {tc.tool_name}
        </span>
        <span
          className="min-w-0 flex-1 truncate font-mono text-machine text-tertiary"
          title={summary}
        >
          {summary}
        </span>
        <span className={cx("shrink-0 text-meta", result.tone)}>
          {result.label}
        </span>
        <Icon
          name="chevron"
          size={12}
          className={cx(
            "shrink-0 text-tertiary transition-transform duration-150",
            open && "rotate-180"
          )}
        />
      </button>

      {open && (
        <div className="space-y-2 border-t border-rule px-2 py-2">
          <div>
            <div className="mb-1 text-meta text-tertiary">入参</div>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-well bg-surface-page/60 p-2 font-mono text-code text-secondary">
              {pretty(tc.input)}
            </pre>
          </div>
          <div>
            <div className="mb-1 text-meta text-tertiary">出参</div>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-well bg-surface-page/60 p-2 font-mono text-code text-secondary">
              {outputText(tc)}
            </pre>
          </div>
          {tc.error && (
            <div className="text-meta text-state-danger">原因：{tc.error}</div>
          )}
        </div>
      )}
    </div>
  );
}
