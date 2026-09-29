import { useEffect } from "react";
import { eventsUrl } from "../api/client";
import { SSEvent, SSEventType } from "../types";

/**
 * 浏览器侧唯一订阅入口：EventSource 只把带 `event:` 字段的消息派发给注册过的类型，
 * 没注册的类型直接丢——所以这张表漂了不报错，只是时间线静默少一类事件（#92 的根因）。
 * 现役清单的唯一权威是 `docs/architecture.md` §3.4 的事件表；`trace_end` 不在表里也不在
 * 这里，它由 `TraceRecorder.close()` 直写 trace JSONL、从不进 EventBus（只在 trace 回放
 * 面出现，故仍留在 `SSEventType` 联合里）。逐条对齐由 `scripts/check-sse-event-registry.mjs`
 * 机械断言（`npm run check:sse`，CI frontend job 同一条命令）。
 */
const EVENT_TYPES: SSEventType[] = [
  "task_created",
  "plan_update",
  "step_start",
  "tool_call",
  "tool_result",
  "tool_circuit_open",
  "context_compressed",
  "task_rollback", // P1-B 工作区回滚完成
  "human_confirm_required",
  "human_confirm_resolved",
  "artifact_created",
  "verification", // P0-B 完成验证结论
  "final_answer",
  "task_completed",
  "task_failed",
  "task_interrupted",
  "task_resumed", // P3 从检查点续跑启动
  "tool_result_evicted", // T1.4 旧的大段 tool 结果换成占位
  "risk_report", // P1: {items, policy, semantic_enabled}
  "risk_found", // P1: RiskItem
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
