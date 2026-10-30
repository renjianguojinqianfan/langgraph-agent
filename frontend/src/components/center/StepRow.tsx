import { useEffect, useState } from "react";
import type { StepRecord } from "../../types";
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
  useEffect(() => {
    if (step.status === "running") setOpen(true);
  }, [step.status]);

  const status = STATUS[step.status] ?? {
    label: step.status,
    dot: "bg-tertiary",
  };
  const toolCount = step.tool_calls.length;
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
        {toolCount > 0 && (
          <span className="shrink-0 font-mono text-machine tabular-nums text-tertiary">
            {toolCount} 次
          </span>
        )}
      </DisclosureHead>

      {open && (
        <div className="flex flex-col gap-1.5 pb-2 pl-5 pr-1">
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
