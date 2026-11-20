import { useMemo } from "react";
import {
  diffPrefix,
  flattenHunks,
  splitRows,
  type DiffKind,
  type DiffMode,
} from "../../lib/changes";
import { DIFF_BODY_TONE, DIFF_TONE } from "../../lib/diffTone";
import type { ChangeDetail } from "../../types";

/** Read-only unified/split body for one change detail (#343). */
export function DiffView({
  detail,
  mode,
  wrap,
}: {
  detail: ChangeDetail;
  mode: DiffMode;
  wrap: boolean;
}) {
  const rows = useMemo(() => flattenHunks(detail.hunks), [detail.hunks]);
  const split = useMemo(() => splitRows(rows), [rows]);
  const lineClass = wrap
    ? "whitespace-pre-wrap break-words "
    : "whitespace-pre ";

  if (mode === "split") {
    return (
      <div className="min-h-0 overflow-auto rounded-well bg-surface-well font-mono text-code">
        <div className={wrap ? "min-w-[36rem]" : "min-w-[44rem]"}>
          {split.map((row, i) => {
            const oldTone: DiffKind = row.oldText === null ? "context" : "delete";
            const newTone: DiffKind = row.newText === null ? "context" : "add";
            return (
              <div
                key={i}
                className="grid grid-cols-[2.5rem_minmax(0,1fr)_2.5rem_minmax(0,1fr)] gap-x-1 px-2"
              >
                <span className="select-none text-right tabular-nums text-tertiary">
                  {row.oldLine ?? ""}
                </span>
                <span className={lineClass + DIFF_TONE[oldTone]}>
                  {row.oldText ?? " "}
                </span>
                <span className="select-none text-right tabular-nums text-tertiary">
                  {row.newLine ?? ""}
                </span>
                <span className={lineClass + DIFF_TONE[newTone]}>
                  {row.newText ?? " "}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-0 overflow-auto rounded-well bg-surface-well font-mono text-code">
      <div className={wrap ? "min-w-0" : "min-w-max"}>
        {rows.map((row, i) => (
          <div
            key={i}
            className="grid grid-cols-[3rem_3rem_1.25rem_minmax(0,1fr)] gap-x-1 px-2"
          >
            <span className="select-none text-right tabular-nums text-tertiary">
              {row.oldLine ?? ""}
            </span>
            <span className="select-none text-right tabular-nums text-tertiary">
              {row.newLine ?? ""}
            </span>
            <span aria-hidden="true" className={DIFF_TONE[row.kind]}>
              {diffPrefix(row.kind)}
            </span>
            <span className={lineClass + DIFF_BODY_TONE[row.kind]}>
              {row.text || " "}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
