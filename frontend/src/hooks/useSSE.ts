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
  "step_start",
  "tool_call",
  "tool_result",
  "context_compressed",
  "final_answer",
  "task_failed",
  "task_interrupted",
  "task_resumed", // P3 / #139 从检查点续跑、或多轮续说启动
  "tool_result_evicted", // T1.4 旧的大段 tool 结果换成占位
  "subtask_start", // #70-A: 子任务派生（父频道）
  "subtask_result", // #70-A: 子任务回传（父频道）
  "subtask_failed", // #70-A: 子任务失败（父频道）
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
        // The wire frame carries only `type` and `data` (backend/api/sse.py), so
        // the event page's clock is the arrival time, not a publisher timestamp.
        // wire → `SSEvent` 的唯一折点：EventSource 只给字符串 `type` 与未校验的 JSON，
        // 判别联合保证的是消费侧，这里保证不了——显式断言一次；事件词汇表本身由
        // `npm run check:sse` 在发布点 / 注册表 / 类型 / 文档四处对齐。
        onEvent({
          type: e.type as SSEventType,
          data: JSON.parse(e.data),
          ts: Date.now() / 1000,
        } as SSEvent);
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
