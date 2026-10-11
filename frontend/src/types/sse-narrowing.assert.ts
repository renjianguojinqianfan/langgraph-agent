/**
 * 类型级断言（#91 AC3 返工）——本文件不被任何运行时代码引用，它的唯一消费者是
 * `npm run typecheck`（tsconfig `include: ["src"]` 覆盖它）。
 *
 * 为什么需要它：票面第 3 条抱怨的是「后端改字段名，前端 tsc 不会响」。把 `SSEvent`
 * 收成按 `type` 判别的联合只是**必要不充分**——如果哪天有人把成员退回 `data: any`，
 * 具名字段照样编得过，收窄悄悄失效而门禁全绿。下面两条 `@ts-expect-error` 就是为这个
 * 场景设的引线：`data` 一旦退回 `any`，那两行就不再报错 → 指令变成「未使用的
 * `@ts-expect-error`」→ `tsc` 立刻红。
 */
import type { ConfirmOutcome, SSEvent } from "../types";

/** 收窄成立时：按 `type` 分支后拿到的是具名载荷，字段名是编译期事实。 */
export function confirmPayloadIsNamed(ev: SSEvent): string {
  if (ev.type === "human_confirm_required") {
    const required: { tool_call_id: string; tool_name: string } = ev.data;
    return `${required.tool_call_id}:${required.tool_name}`;
  }
  if (ev.type === "human_confirm_resolved") {
    const outcome: ConfirmOutcome = ev.data.outcome;
    return outcome;
  }
  return ev.type;
}

// 引线：`@ts-expect-error` 必须紧贴出错那一行（跨行的 IIFE 会让指令落空 → TS2578）。
declare const wireEvent: SSEvent;

// @ts-expect-error `human_confirm_resolved` 的载荷上没有 `not_a_field`：报错即断言成立
const resolvedMissing: string = wireEvent.type === "human_confirm_resolved" ? wireEvent.data.not_a_field : "";

// @ts-expect-error `human_confirm_required` 的载荷上没有 `outcome`（它是 resolved 的字段）
const requiredWrongField: string = wireEvent.type === "human_confirm_required" ? wireEvent.data.outcome : "";

export const narrowingTripwires = [resolvedMissing, requiredWrongField].length;
