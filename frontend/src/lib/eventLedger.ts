import type { SSEvent, SSEventType } from "../types";

/** How many events the EventPane renders — the store keeps its own 500-event
 * buffer; this cap is the user-visible ledger window (D-29). */
export const LEDGER_LIMIT = 100;

/** Which SSE types belong in the user-visible ledger (D-29).
 *
 * `heartbeat` is the only exclusion today: it is transport keep-alive, not a
 * fact about the task. Everything else keeps the current behaviour. */
export function isLedgerType(type: SSEventType): boolean {
  return type !== "heartbeat";
}

/** Derive the EventPane list from the store buffer (D-29).
 *
 * SSE replays its buffer on reconnect, so the same fact arrives again: key by
 * `type + JSON payload` and keep the latest arrival. Output is the newest-first
 * ledger window (the pane shows "most recent first"), capped at `limit`. */
export function ledgerEvents(
  events: readonly SSEvent[],
  limit: number = LEDGER_LIMIT
): SSEvent[] {
  const latest = new Map<string, { ev: SSEvent; at: number }>();
  events.forEach((ev, at) => {
    latest.set(`${ev.type}:${JSON.stringify(ev.data)}`, { ev, at });
  });

  return [...latest.values()]
    .filter((entry) => isLedgerType(entry.ev.type))
    .sort((a, b) => a.at - b.at)
    .map((entry) => entry.ev)
    .slice(-limit)
    .reverse();
}