import { useEffect, useState } from "react";
import type { StepRecord, TraceMarker } from "../../types";
import { CompressMarker } from "./CompressMarker";
import { DisclosureHead } from "./DisclosureHead";
import { StepRow } from "./StepRow";

interface TurnProcessProps {
  steps: StepRecord[];
  /** Markers owned by these steps; ownership is resolved in Conversation. */
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
  for (const marker of markers) {
    const index = marker.data.step_index;
    byStep.set(index, [...(byStep.get(index) ?? []), marker]);
  }
  if (!steps.length) return null;

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
        </div>
      )}
    </section>
  );
}
