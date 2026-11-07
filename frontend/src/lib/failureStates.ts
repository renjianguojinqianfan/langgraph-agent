/**
 * #256: failure, loading and empty must never look alike.
 *
 * Every function here is a pure decision: the caller owns the fetch and the
 * copy, this module owns "which of the four faces may show". The panes stay
 * dumb, the rules stay testable in the node-only vitest environment (same
 * seam as `preview.ts` / `taskState.ts`).
 */

import type { Task } from "../types";

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
 * Whether the one-line error banner rides above the list. It shows on every
 * face except the full error face (which already carries the copy and the
 * entry) — including the search-emptied "empty": gating on "rows" hid the
 * banner, and the retry with it, the moment the query filtered every row
 * away (复审 LOW).
 */
export function listBannerVisible(error: string, state: HistoryListState): boolean {
  return !!error && state !== "error";
}

/**
 * The empty face's title. "No tasks at all" and "tasks exist, none match the
 * query" are different truths — and the second one, with a failed refetch on
 * top, is exactly the 搜索过滤空 + 刷新失败 combination where
 * "这里还没有任务" was half a lie (复审 LOW-1).
 */
export function listEmptyTitle(rows: number): string {
  return rows > 0 ? "没有匹配的任务" : "这里还没有任务";
}

/**
 * #256: what one list answer means. `req()` never rejects on a non-2xx, so a
 * JSON-bodied 500 (persistence failure) or 401 (expired token) used to arrive
 * as an envelope without `tasks` and the caller silently kept the empty face.
 * A 2xx *with* `tasks` is the only success — including a genuinely empty
 * list, which is a real answer and shows the empty face.
 */
export type ListLoadOutcome =
  | { ok: true; tasks: Task[] }
  | { ok: false; error: string; authExpired: boolean };

export function listLoadOutcome(status: number, tasks: Task[] | null): ListLoadOutcome {
  if (status >= 200 && status < 300 && tasks) return { ok: true, tasks };
  return { ok: false, error: listLoadFailure(status), authExpired: status === 401 };
}

/**
 * Why the list read failed. 401 is its own face: the stored token expired,
 * and reloading keeps the dead one (AuthGuard only checks that a token
 * exists), so the next step is to drop it and log in again — never "刷新页面".
 */
export function listLoadFailure(status: number): string {
  switch (status) {
    case 0:
      return "无法连接后台；确认服务在运行后再试。";
    case 401:
      return "登录已失效；重新登录后再试。";
    default:
      return status >= 200 && status < 300
        ? "任务列表响应异常；请重试。"
        : `任务列表读取失败（HTTP ${status}）。`;
  }
}

/**
 * #256: a selected task whose snapshot never arrived is not a new task.
 * Every branch here is a failure; none may fall through to the "新任务"
 * face. The buckets stay attributed separately (design.md §7): `missing`
 * (404) vs `auth` (401) vs `unreachable` (the request never arrived — a 500
 * means the server IS running, so it must not read as "连不上后台") vs
 * `server` (any other non-2xx) vs `bad-response` (a 2xx whose envelope
 * carries no snapshot — a wrong `VITE_API_BASE`, an SPA-fallback HTML page,
 * a gateway error body: still a failed load, never a spinner forever).
 */
export type DetailLoadState =
  | "ready"
  | "missing"
  | "auth"
  | "unreachable"
  | "server"
  | "bad-response";

export function detailLoadState(status: number, hasData: boolean): DetailLoadState {
  if (status >= 200 && status < 300) return hasData ? "ready" : "bad-response";
  if (status === 404) return "missing";
  if (status === 401) return "auth";
  if (status === 0) return "unreachable";
  return "server";
}

/**
 * Copy for the center column when the selected task's snapshot failed. Every
 * failure state must produce a non-empty line: the caller stores the copy as
 * the `detailError` entry, and an empty string deletes that entry — which is
 * how a 2xx-without-envelope used to park the column on "加载中…" with no
 * error face and no retry (复审回归).
 */
export function detailLoadFailure(state: DetailLoadState, status = 0): string {
  switch (state) {
    case "missing":
      return "任务不存在或已被删除。";
    case "auth":
      return "登录已失效；重新登录后再试。";
    case "unreachable":
      return "无法连接后台；确认服务在运行后再试。";
    case "server":
      return `服务端出错（HTTP ${status}）；稍后重试。`;
    case "bad-response":
      return "任务响应异常；请重试。";
    default:
      return "";
  }
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
 * file that cannot be read. A 401 is the dead token — the retry button
 * cannot fix it, so the copy points at re-login. A 2xx with an unusable
 * envelope is its own wording — "HTTP 200" in a failure line reads as a
 * contradiction.
 *
 * The 409 fallback is cause-agnostic on purpose: `#280` maps every read
 * failure (UnicodeDecodeError *and* OSError — a locked file, a permission
 * error) onto 409 with a sanitized detail, so a copy that names one cause
 * would mis-attribute the others. When the server does send a detail it is
 * already sanitized and gets shown verbatim.
 */
export function planLoadFailure(status: number, detail?: string): string {
  switch (status) {
    case 0:
      return "无法连接后台；确认服务在运行后重试。";
    case 401:
      return "登录已失效；重新登录后再试。";
    case 404:
      return "任务不存在或已被删除。";
    case 409:
      return detail?.trim()
        ? `计划文件无法读取：${detail.trim()}`
        : "计划文件无法读取；请稍后重试，若持续失败查看服务端日志。";
    default:
      return status >= 200 && status < 300
        ? "计划响应异常；请重试。"
        : `计划读取失败（HTTP ${status}）。`;
  }
}

/**
 * Why the workspace file list read failed, with the next step (D-31 / #258
 * AC5). Same buckets as `previewFailure`: the server separates them by status,
 * and a single generic line was exactly the unreadable copy the review
 * flagged. `detail` is the sanitized FastAPI detail when the server sent one.
 */
export function filesLoadFailure(status: number, detail?: string): string {
  switch (status) {
    case 0:
      return "无法连接后台；确认服务在运行后点「重试」。（切换页签/重新打开任务也会重新加载）";
    case 401:
      return "登录已失效；重新登录后再点「重试」。";
    case 404:
      return "任务不存在或已被删除；返回任务列表重新选择。";
    case 409:
      return "任务工作区不存在；检查任务目录是否还在，或切回存在的目录/新建任务。";
    case 400:
      return "文件列表请求非法（路径越界或参数错误）；核对任务工作区后点「重试」。";
    case 415:
      return "响应内容类型不受支持；点「重试」，若持续失败查看服务端日志。";
    default:
      return status >= 200 && status < 300
        ? "文件列表响应异常；点「重试」。（切换页签/重新打开任务也会重新加载）"
        : `文件列表读取失败（HTTP ${status}）${
            detail?.trim() ? `：${detail.trim()}` : ""
          }；点「重试」或查看服务端日志。`;
  }
}

/** What the workspace file pane may show. Same discipline as the history
 * list (#256): a failed read is never also the empty face. */
export type FilesPaneState = "error" | "loading" | "empty" | "rows";

export function filesPaneState(input: {
  error: string;
  loading: boolean;
  count: number;
}): FilesPaneState {
  // Same rule as historyListState: rows already on screen stay rows — a failed
  // refresh is told by the persistent live region, not by swapping the loaded
  // list for an error face. Only an error with no rows owns the pane.
  if (input.count > 0) return "rows";
  if (input.error) return "error";
  if (input.loading) return "loading";
  return "empty";
}

/** What the file pane's live region must say. A non-empty note wins on every
 * face — including `rows`, where the list stays and only the failure prompt
 * changes (a refresh/poll failure must not blank the reason, #258 终审). */
export function filesPaneStatusText(state: FilesPaneState, note: string): string {
  if (note) return note;
  void state;
  return "";
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
