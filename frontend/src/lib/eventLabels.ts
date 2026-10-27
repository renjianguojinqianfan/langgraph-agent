import type { SSEventType } from "../types";

/**
 * SSE 流上会出现的类型：联合里唯一不进流的是 `trace_end`（只写 trace JSONL、
 * 不经 EventBus，见 `docs/architecture.md` §3.4「唯一不对称」）。
 */
export type SseDeliveredType = Exclude<SSEventType, "trace_end">;

/**
 * 事件页（`EventPane`）的中文标签：SSE 事件词汇表的**显示投影**，不是独立权威源。
 *
 * - 键集必须与 `frontend/src/hooks/useSSE.ts` 的 `EVENT_TYPES` 逐条相等（条数以那张表为准，
 *   这里不写计数——数字会腐）：`Record<SseDeliveredType, string>` 让 tsc 抓住缺项/多项，
 *   `npm run check:sse` 的「断言 4」再机械比对一次——增删事件类型时漏改这里会直接变红，
 *   而不是让事件页静默显示英文类型名（#229）。
 */
export const EVENT_LABELS: Record<SseDeliveredType, string> = {
  task_created: "任务创建",
  task_resumed: "任务续跑",
  step_start: "步骤开始",
  tool_call: "工具调用",
  tool_result: "工具结果",
  tool_result_evicted: "结果被驱逐",
  context_compressed: "上下文压缩",
  final_answer: "最终回答",
  task_failed: "任务失败",
  task_interrupted: "任务停下",
  subtask_start: "子任务开始",
  subtask_result: "子任务结果",
  subtask_failed: "子任务失败",
  heartbeat: "心跳",
};

/**
 * 类型 → 中文标签。线上帧的 `type` 是未校验的字符串（`useSSE.ts` 只做一次断言），
 * 认不出来的类型回退为原始类型名，而不是让事件页出现 `undefined`。
 */
export function eventLabel(type: SSEventType | string): string {
  return EVENT_LABELS[type as SseDeliveredType] ?? type;
}
