import type { ConfirmDialogState } from "../types";

/**
 * #91 AC1（G2 加固）：确认框是**全局单格**，收口 / 切任务都只动这一格。这两条纯函数
 * 是这条不变量的唯一判据，store 与 `scripts/check-confirm-face.mjs` 共用同一份，避免
 * 「测试跑一套、线上另一套」。之所以单拎出来：`taskStore.ts` 顶层 `create()` 带 zustand
 * 与浏览器态，无法在纯 Node 里跑；这两个函数只 `import type`（编译期擦除），可被
 * `node --experimental-strip-types` 直接执行，从而在没有测试框架的本仓给 AC1 一条真断言。
 */

/**
 * 收到 `human_confirm_resolved` 时该不该关掉当前框。
 *
 * 三条一起成立才关：框开着 + **这条 resolved 属于当前框的那个任务** + 同一个 tool_call。
 * 缺了 task_id 比对就会跨任务串味（#91 的 bug）：切走 A 后在 B 视图收到 A 的 resolved，
 * 只比 tool_call_id 会误收 B 的框、或让 A 的僵尸框盖在 B 上——后端自行收口的终态不会
 * 有人来点按钮，留着就是一具能对已判完的调用按「批准」的假弹窗。
 */
export function shouldCloseConfirmOnResolved(
  confirm: ConfirmDialogState,
  resolvedTaskId: string | undefined,
  resolvedToolCallId: string | undefined
): boolean {
  return (
    confirm.open &&
    confirm.task_id === resolvedTaskId &&
    confirm.tool_call_id === resolvedToolCallId
  );
}

/**
 * 切换当前任务时对确认框的处理：属于**别的任务**的开着的框要清掉，同任务的框保持
 * （例如刷新后又选中同一任务）。这是「切走任务、后端才收口」那格残留的正面收口。
 */
export function confirmAfterSwitch(
  confirm: ConfirmDialogState,
  nextTaskId: string | null
): ConfirmDialogState {
  if (confirm.open && confirm.task_id !== nextTaskId) return { open: false };
  return confirm;
}
