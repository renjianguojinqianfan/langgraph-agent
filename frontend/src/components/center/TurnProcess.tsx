import { useEffect, useState } from "react";
import { compressFaceParts, type StepRecord, type TraceMarker } from "../../types";
import { cx } from "../../lib/cx";
import { DisclosureHead } from "./DisclosureHead";
import { StepRow } from "./StepRow";

function CompressMarker({ marker }: { marker: TraceMarker }) {
  const d = marker.data;
  return (
    <div className="px-1 py-1 text-machine text-tertiary">
      上下文已压缩：{compressFaceParts(d)}
      {d.strategy ? `，策略 ${d.strategy}` : ""}
      {d.context_tokens != null ? `（剩余 ${d.context_tokens} tokens）` : ""}
    </div>
  );
}

interface TurnProcessProps {
  steps: StepRecord[];
  /** Markers this block owns; markers no step owns render once at the end. */
  markers: TraceMarker[];
  /** The round currently running: opens by itself and keeps live status. */
  live?: boolean;
  /** Legacy records have no round; name the block instead of guessing one. */
  title?: string;
}

/**
 * One answer's execution disclosure (#265), modelled on dsh's turn-process:
 * a collapsed "已完成 · N 步" header above the answer, expanding to only this
 * round's steps. The turn number itself is never rendered.
 */
export function TurnProcess({
  steps,
  markers,
  live = false,
  title,
}: TurnProcessProps) {
  const [open, setOpen] = useState(live);

  // Follow the live round: open while it runs, fold once it settles (dsh).
  // A manual toggle in between is respected until the next transition.
  useEffect(() => {
    setOpen(live);
  }, [live]);

  const byStep = new Map<number, TraceMarker[]>();
  const loose: TraceMarker[] = [];
  for (const marker of markers) {
    const index = marker.data.step_index;
    if (steps.some((step) => step.index === index)) {
      byStep.set(index, [...(byStep.get(index) ?? []), marker]);
    } else {
      loose.push(marker);
    }
  }
  if (!steps.length && !loose.length) return null;

  const failed = steps.some((step) => step.status === "failed");
  const running = live || steps.some((step) => step.status === "running");
  const toolCount = steps.reduce((n, step) => n + step.tool_calls.length, 0);
  const label = title ?? (failed ? "处理失败" : running ? "执行中" : "已完成");
  const dot = failed
    ? "bg-state-danger"
    : running
      ? "bg-signal-now animate-pulse"
      : "bg-tertiary";

  return (
    <section aria-label={label} className="border-y border-rule">
      <DisclosureHead
        open={open}
        onToggle={() => setOpen((value) => !value)}
        dot={dot}
      >
        <span className="text-body text-secondary">{label}</span>
        {steps.length > 0 && (
          <span className="font-mono text-machine tabular-nums text-tertiary">
            {steps.length} 步 · {toolCount} 次工具调用
          </span>
        )}
      </DisclosureHead>

      {open && (
        <div className="pb-1">
          {steps.map((step) => (
            <div key={step.index}>
              <StepRow step={step} />
              {(byStep.get(step.index) ?? []).map((marker, i) => (
                <CompressMarker key={i} marker={marker} />
              ))}
            </div>
          ))}
          {loose.map((marker, i) => (
            <CompressMarker key={i} marker={marker} />
          ))}
        </div>
      )}
    </section>
  );
}
