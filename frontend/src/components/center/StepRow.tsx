import { useEffect, useId, useState } from "react";
import { cx } from "../../lib/cx";
import { LONG_TEXT } from "../../lib/longContent";
import { stepHasReasoning } from "../../lib/reasoning";
import type { StepRecord } from "../../types";
import { Icon } from "../ui/Icon";
import { DisclosureHead } from "./DisclosureHead";
import { ToolRow } from "./ToolRow";

const STATUS: Record<string, { label: string; dot: string }> = {
  pending: { label: "等待中", dot: "bg-rule-strong" },
  running: { label: "运行中", dot: "bg-signal-now animate-pulse" },
  done: { label: "已完成", dot: "bg-tertiary" },
  failed: { label: "失败", dot: "bg-state-danger" },
};

export function StepRow({
  step,
  taskId,
}: {
  step: StepRecord;
  /** Parent task identity: switching tasks must fold the thinking block even
   * when the new record's reasoning text happens to be identical (#300 L1). */
  taskId?: string;
}) {
  // Running steps open by themselves so live tool traffic is visible.
  const [open, setOpen] = useState(step.status === "running");
  // D-40: the thinking block starts folded even when the step is expanded; a
  // step without reasoning renders no block at all.
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const reasoningId = useId();
  useEffect(() => {
    if (step.status === "running") setOpen(true);
  }, [step.status]);
  // A replaced record (task switch / refreshed snapshot) starts folded again.
  // The identity deps matter: the same reasoning text under another task or
  // another step index is still a different record (L1).
  useEffect(() => {
    setReasoningOpen(false);
  }, [taskId, step.index, step.reasoning]);

  const status = STATUS[step.status] ?? {
    label: step.status,
    dot: "bg-tertiary",
  };
  const toolCount = step.tool_calls.length;
  const hasReasoning = stepHasReasoning(step);
  const summary =
    step.thought.trim() ||
    step.tool_calls.map((tc) => tc.tool_name).join("、") ||
    "步骤";

  return (
    <div className="border-b border-rule last:border-b-0">
      <DisclosureHead
        open={open}
        onToggle={() => setOpen((v) => !v)}
        dot={status.dot}
      >
        <span className="sr-only">{status.label}</span>
        <span className="min-w-0 flex-1 truncate text-body text-primary" title={summary}>
          {summary}
        </span>
        {hasReasoning && (
          <span className="shrink-0 text-machine text-tertiary">思考</span>
        )}
        {toolCount > 0 && (
          <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
            {toolCount} 次
          </span>
        )}
      </DisclosureHead>

      {open && (
        <div className="flex flex-col gap-1.5 pb-2 pl-5 pr-1">
          {hasReasoning && (
            <div className="rounded-well bg-surface-well">
              <button
                type="button"
                aria-expanded={reasoningOpen}
                aria-controls={reasoningId}
                aria-label={reasoningOpen ? "收起模型思考" : "展开模型思考"}
                onClick={() => setReasoningOpen((value) => !value)}
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-meta text-tertiary"
              >
                <span>思考</span>
                <span className="ml-auto text-machine">
                  {reasoningOpen ? "收起" : "展开"}
                </span>
                <Icon
                  name="chevron"
                  size={12}
                  className={cx(
                    "shrink-0 transition-transform duration-150",
                    reasoningOpen && "rotate-180"
                  )}
                />
              </button>
              <p
                id={reasoningId}
                hidden={!reasoningOpen}
                className={cx(
                  "max-h-60 overflow-y-auto overscroll-contain whitespace-pre-wrap border-t border-rule px-2 py-2 text-meta text-secondary",
                  LONG_TEXT
                )}
              >
                {step.reasoning}
              </p>
            </div>
          )}
          {toolCount === 0 ? (
            <div className="text-meta text-tertiary">本步骤未调用工具。</div>
          ) : (
            step.tool_calls.map((tc) => <ToolRow key={tc.id} tc={tc} />)
          )}
        </div>
      )}
    </div>
  );
}
