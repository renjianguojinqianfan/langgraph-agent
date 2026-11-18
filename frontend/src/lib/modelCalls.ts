import type { SSEvent } from "../types";

/**
 * One `llm_call` trace record (S7 format, S8 read face).
 *
 * The trace appends a `pending=true` placeholder before each HTTP attempt and
 * a final record with the same `(call_id, attempt)` when the attempt ends.
 * Readers therefore keep the LAST line per attempt; a lone placeholder is
 * evidence of an interrupted call, never a fabricated response.
 */
export interface ModelCallRecord {
  call_id: string;
  attempt: number;
  owner_task_id: string;
  source: string;
  round: number | null;
  step: number | null;
  model: string;
  protocol: string;
  started_at: number | null;
  ended_at: number | null;
  status: string;
  request: unknown;
  response: unknown;
  error: string | null;
  truncated: boolean;
  usage: unknown;
  pending: boolean;
}

export interface ModelCall {
  call_id: string;
  /** Unique attempts, ascending; a replayed/overwritten line wins. */
  records: ModelCallRecord[];
  latest: ModelCallRecord;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function recordOf(ev: SSEvent): ModelCallRecord | null {
  if (ev.type !== "llm_call") return null;
  const data = ev.data as Record<string, unknown> | null | undefined;
  if (!data || typeof data !== "object") return null;
  const callId = typeof data.call_id === "string" ? data.call_id : "";
  const attempt = optionalNumber(data.attempt);
  if (!callId || attempt === null || attempt < 1) return null;
  return {
    call_id: callId,
    attempt,
    owner_task_id:
      typeof data.owner_task_id === "string" ? data.owner_task_id : "",
    source: typeof data.source === "string" ? data.source : "",
    round: optionalNumber(data.round),
    step: optionalNumber(data.step),
    model: typeof data.model === "string" ? data.model : "",
    protocol: typeof data.protocol === "string" ? data.protocol : "",
    started_at: optionalNumber(data.started_at),
    ended_at: optionalNumber(data.ended_at),
    status: typeof data.status === "string" ? data.status : "unknown",
    request: data.request,
    response: data.response,
    error: typeof data.error === "string" ? data.error : null,
    truncated: data.truncated === true,
    usage: data.usage,
    pending: data.pending === true,
  };
}

function byStarted(a: ModelCallRecord, b: ModelCallRecord): number {
  return (a.started_at ?? 0) - (b.started_at ?? 0) || a.attempt - b.attempt;
}

/**
 * Group trace events into logical calls. Replayed lines overwrite by
 * `(call_id, attempt)` instead of appending, so reconnect / duplicate lines
 * cannot double-count an attempt or resurrect a placeholder after its final
 * record.
 */
export function collectModelCalls(events: readonly SSEvent[]): ModelCall[] {
  const byCall = new Map<string, Map<number, ModelCallRecord>>();
  for (const event of events) {
    const record = recordOf(event);
    if (!record) continue;
    let attempts = byCall.get(record.call_id);
    if (!attempts) {
      attempts = new Map();
      byCall.set(record.call_id, attempts);
    }
    attempts.set(record.attempt, record);
  }

  const calls: ModelCall[] = [];
  for (const [call_id, attempts] of byCall) {
    const records = [...attempts.values()].sort(byStarted);
    calls.push({ call_id, records, latest: records[records.length - 1] });
  }
  return calls.sort(
    (a, b) =>
      (a.latest.started_at ?? 0) - (b.latest.started_at ?? 0) ||
      a.call_id.localeCompare(b.call_id)
  );
}

/** Record completeness: an unfinished placeholder or a bounded request preview. */
export function callCompleteness(
  call: ModelCall
): "pending" | "truncated" | "complete" {
  if (call.latest.pending) return "pending";
  if (call.latest.truncated) return "truncated";
  return "complete";
}

/** One-line list summary; all values are rendered as text by React. */
export function modelCallSummary(call: ModelCall): string {
  const record = call.latest;
  const who = record.source || "unknown";
  const model = record.model || record.protocol || "?";
  return `${who} · ${model} · ${record.status}`;
}

export function shortCallId(callId: string): string {
  return callId.length > 10 ? callId.slice(0, 10) : callId;
}
