import type { Task } from "../types";
import { isParked, isStoppable } from "./taskState";

/** D-32: the Composer state matrix, derived as a pure function so every branch
 * is testable without a DOM (D-23). The copy below is copied verbatim from the
 * pre-split Composer (#232): behaviour and wording must not change. */
export interface ComposerViewInput {
  task: Task | undefined;
  currentTaskId: string | null;
  detailError: string;
  detailAuthExpired: boolean;
  text: string;
  busy: boolean;
  /** Not one of D-32's six fields: `bottomNote` = disabledReason ?? sendError,
   * so the send-failure line has to be an input. */
  sendError: string | null;
}

export interface ComposerView {
  parked: boolean;
  failed: boolean;
  running: boolean;
  loading: boolean;
  inputDisabled: boolean;
  canSend: boolean;
  disabledReason: string | null;
  placeholder: string;
  bottomNote: string | null;
}

export function composerView(input: ComposerViewInput): ComposerView {
  const { task, currentTaskId, detailError, detailAuthExpired, text, busy, sendError } =
    input;
  // B1 (#219): a failed task is parked too — its checkpoint is intact, so the
  // next message continues the same conversation.
  const parked = task ? isParked(task.status) : false;
  const failed = task?.status === "FAILED";
  const running = task ? isStoppable(task.status) : false;
  // A selected task whose snapshot has not arrived yet must not be treated as
  // "new task": sending would silently create a second task.
  const loading = !!currentTaskId && !task && !detailError;
  const inputDisabled = !!detailError || loading || (!!task && !parked);
  const canSend = !busy && !inputDisabled && !!text.trim();

  // One reason line under the input; the placeholder only carries the action
  // (new task / continue) and never repeats the disable reason.
  const disabledReason = detailError
    ? detailAuthExpired
      ? "登录已失效；重新登录后再试"
      : "任务加载失败；重试或新建任务"
    : loading
      ? "正在加载任务…"
      : task?.status === "PENDING"
        ? "任务启动中，稍等片刻…"
        : task?.status === "RUNNING"
          ? "任务运行中；停下或等这一轮结束再发"
          : null;
  const placeholder =
    loading || detailError
      ? "…"
      : !task
        ? "用自然语言描述一个任务，例如：联网查资料并写一份报告…"
        : failed
          ? "任务出错，接着说一句继续…"
          : parked
            ? "接着说一句，任务从这里继续…"
            : "…";

  return {
    parked,
    failed,
    running,
    loading,
    inputDisabled,
    canSend,
    disabledReason,
    placeholder,
    bottomNote: disabledReason ?? sendError,
  };
}

/** Only clear the draft that was actually sent: text typed while the request
 * was in flight must survive (#232 contract, verbatim from Composer). */
export function clearedDraft(prev: string, sent: string): string {
  return prev.trim() === sent ? "" : prev;
}
