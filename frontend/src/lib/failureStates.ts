/**
 * #256: failure, loading and empty must never look alike.
 *
 * Every function here is a pure decision: the caller owns the fetch and the
 * copy, this module owns "which of the four faces may show". The panes stay
 * dumb, the rules stay testable in the node-only vitest environment (same
 * seam as `preview.ts` / `taskState.ts`).
 */

/** What the history list may show. A failed load is never an empty list. */
export type HistoryListState = "error" | "loading" | "empty" | "rows";

/**
 * `rows` counts the server's answer (unfiltered); `visible` counts the rows
 * after the search box. A failed refetch keeps the rows the last success
 * produced — with none, the error owns the pane instead of the empty copy,
 * which would read as "this directory has no tasks" (the #256 bug).
 */
export function historyListState(input: {
  error: string;
  loading: boolean;
  rows: number;
  visible: number;
}): HistoryListState {
  if (input.rows === 0) {
    if (input.error) return "error";
    if (input.loading) return "loading";
    return "empty";
  }
  return input.visible === 0 ? "empty" : "rows";
}

/**
 * #256: a selected task whose snapshot never arrived is not a new task.
 * `missing` (404) and `unreachable` (transport / server) are both failures;
 * neither may fall through to the "新任务" face.
 */
export type DetailLoadState = "ready" | "missing" | "unreachable";

export function detailLoadState(status: number): DetailLoadState {
  if (status === 404) return "missing";
  if (status >= 200 && status < 300) return "ready";
  return "unreachable";
}

/** Copy for the center column when the selected task's snapshot failed. */
export function detailLoadFailure(state: DetailLoadState): string {
  return state === "missing"
    ? "任务不存在或已被删除。"
    : "无法连接后台；确认服务在运行后再试。";
}

/**
 * #256: a failed plan read must not render as an empty plan — the empty face
 * is what invited the empty `PUT` that overwrote the real file on disk.
 * `ready` also gates editing: an unread plan may not be saved over.
 */
export type PlanLoadState = "ready" | "error";

export function planLoadState(status: number): PlanLoadState {
  return status >= 200 && status < 300 ? "ready" : "error";
}

/**
 * Why the plan read failed. A missing plan *file* is not a failure (the
 * backend answers 200 with empty text); these are the read failures:
 * `backend/api/routes.py` raises 404 for a vanished task and 409 for a plan
 * file that is not the agreed UTF-8 Markdown.
 */
export function planLoadFailure(status: number, detail?: string): string {
  switch (status) {
    case 0:
      return "无法连接后台；确认服务在运行后重试。";
    case 404:
      return "任务不存在或已被删除。";
    case 409:
      return `计划文件无法读取：${detail?.trim() || "内容不是约定的 UTF-8 Markdown。"}`;
    default:
      return `计划读取失败（HTTP ${status}）。`;
  }
}

/**
 * #256: the login page must tell "wrong secret" from "cannot reach the
 * backend" — retrying a password never fixes a dead server.
 */
export type LoginOutcome = "ok" | "wrong-secret" | "unreachable";

export function loginOutcome(status: number, ok: boolean): LoginOutcome {
  // 0 = the request never reached the backend (fetch threw).
  if (status === 0) return "unreachable";
  // The backend rejects a bad secret with 401 (`backend/api/routes.py`).
  if (status === 401) return "wrong-secret";
  if (status >= 200 && status < 300 && ok) return "ok";
  // 5xx and anything unexpected: do not blame the secret.
  return "unreachable";
}
