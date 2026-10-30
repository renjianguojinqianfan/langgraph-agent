import { compressFaceParts, type TraceMarker } from "../../types";

export function CompressMarker({ marker }: { marker: TraceMarker }) {
  const d = marker.data;
  return (
    <div className="px-1 py-1 text-machine text-tertiary">
      上下文已压缩：{compressFaceParts(d)}
      {d.strategy ? `，策略 ${d.strategy}` : ""}
      {d.context_tokens != null ? `（剩余 ${d.context_tokens} tokens）` : ""}
    </div>
  );
}
