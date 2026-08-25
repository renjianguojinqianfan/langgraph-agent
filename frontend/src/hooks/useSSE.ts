import { useEffect } from "react";
import { eventsUrl } from "../api/client";
import { SSEvent, SSEventType } from "../types";

const EVENT_TYPES: SSEventType[] = [
  "task_created",
  "plan_update",
  "step_start",
  "tool_call",
  "tool_result",
  "tool_circuit_open",
  "context_compressed",
  "human_confirm_required",
  "artifact_created",
  "final_answer",
  "task_completed",
  "task_failed",
  "task_interrupted",
  "risk_report", // P1: {items, policy, semantic_enabled}
  "risk_found", // P1: RiskItem
  "subtask_start", // P1: {subtask_id, name, status, parent_task_id}
  "subtask_result", // P1: {subtask_id, name, status, summary, artifacts}
  "subtask_failed", // P1: {subtask_id, name, error}
  "trace_end",
  "heartbeat",
];

/**
 * Subscribe to a task's SSE event stream and forward each parsed event to
 * ``onEvent``. Re-subscribes when ``taskId`` changes. The URL carries the
 * auth token as ``?token=`` when present (P1 item 5).
 */
export function useSSE(taskId: string | null, onEvent: (ev: SSEvent) => void) {
  useEffect(() => {
    if (!taskId) return;
    const es = new EventSource(eventsUrl(taskId));
    const handler = (e: MessageEvent) => {
      try {
        onEvent({ type: e.type as SSEventType, data: JSON.parse(e.data) });
      } catch {
        /* ignore malformed frames */
      }
    };
    EVENT_TYPES.forEach((t) => es.addEventListener(t, handler as EventListener));
    es.onerror = () => {
      // The browser automatically attempts to reconnect; nothing to do here.
    };
    return () => es.close();
  }, [taskId, onEvent]);
}
