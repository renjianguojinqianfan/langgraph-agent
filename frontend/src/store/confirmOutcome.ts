import type { ConfirmOutcome, StepRecord } from "../types";

/**
 * #91 AC2 的实时面：`human_confirm_resolved` 带的终态并回 step 记录。
 *
 * `aborted` 档非回填不可：被 stop 打断的 run 到不了 tool 节点，所以它**没有 `tool_result`**
 * （后端 `nodes.py` 的 `human_confirm_node` 自述，`test_confirm_outcomes.py` 也钉着这条）。
 * 少了这一步，这一档在 Step 详情里实时停在「待确认」，要等重新选中任务读快照才对得上；
 * TraceTab 读的是事件原文，不受影响。
 *
 * 为什么单拎纯函数（与 `confirmSlot.ts` 同一个理由）：`taskStore.ts` 顶层 `create()` 带
 * zustand/React，plain-Node 断言跑不了它；这里只 `import type`（编译期擦除），
 * 能被 `scripts/check-confirm-face.mjs` 直接 import 断言。store 那侧的调用点属接线，
 * 不在那条断言的覆盖范围内。
 */
export function withConfirmOutcome(
  steps: StepRecord[],
  resolvedToolCallId: string,
  outcome: ConfirmOutcome
): StepRecord[] {
  const hit = steps.some((s) => s.tool_calls.some((tc) => tc.id === resolvedToolCallId));
  if (!hit) {
    // 一条都不匹配（含「已切到 B、收到 A 的 resolved」）：原引用返回，不给别的任务写脏副本。
    return steps;
  }
  return steps.map((s) => ({
    ...s,
    tool_calls: s.tool_calls.map((tc) =>
      tc.id === resolvedToolCallId ? { ...tc, confirm_outcome: outcome } : tc
    ),
  }));
}
