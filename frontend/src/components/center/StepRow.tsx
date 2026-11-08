import { useEffect, useState } from "react";
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

export function StepRow({ step }: { step: StepRecord }) {
  // Running steps open by themselves so live tool traffic is visible.
  const [open, setOpen] = useState(step.status === "running");
  // D-40: the thinking block starts folded even when the step is expanded; a
  // step without reasoning renders no block at all.
  const [reasoningOpen, setReasoningOpen] = useState(false);
  useEffect(() => {
    if (step.status === "running") setOpen(true);
  }, [step.status]);
  // A replaced record (task switch / refreshed snapshot) starts folded again;
  // an unchanged reasoning string keeps the reader's current expansion.
  useEffect(() => {
    setReasoningOpen(false);
  }, [step.reasoning]);

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
            <div className="rounded-well border border-rule bg-surface-well">
              <button
                type="button"
                aria-expanded={reasoningOpen}
                aria-label="展开模型思考"
                onClick={() => setReasoningOpen((value) => !value)}
                className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-meta text-tertiary"
              >
                <Icon
                  name="chevron"
                  size={12}
                  className={cx(
                    "transition-transform duration-150",
                    reasoningOpen && "rotate-180"
                  )}
                />
                <span>思考</span>
                <span className="ml-auto text-machine">
                  {reasoningOpen ? "收起" : "展开"}
                </span>
              </button>
              {reasoningOpen && (
                <p
                  className={cx(
                    "max-h-60 overflow-y-auto overscroll-contain whitespace-pre-wrap px-2 pb-2 text-meta text-secondary",
                    LONG_TEXT
                  )}
                >
                  {step.reasoning}
                </p>
              )}
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
