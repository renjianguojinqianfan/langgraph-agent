// Mirrors backend/api/schemas.py field names exactly.

export type TaskStatus =
  | "PENDING"
  | "RUNNING"
  | "FAILED"
  | "INTERRUPTED";

/**
 * Sticky per-task mode (ADR 0008 decision 3): plan or build. Default build.
 * There is no COMPLETED status any more — a round ending is a "停下"
 * (INTERRUPTED) and the task can always be continued (decision 5).
 */
export type TaskMode = "plan" | "build";

/**
 * Confirmation-gate / risk-scan faces were removed in slice S4 (#138); the
 * danger hook (`backend/core/agent/policy.py`, S2) is the only guard left.
 */

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface PlanStep {
  index: number;
  description: string;
  status: string; // pending | active | done
}

export interface ToolCallRecord {
  id: string;
  tool_name: string;
  input: Record<string, any>;
  output: any;
  status: string; // pending | success | failed | skipped
  error?: string | null;
}

export interface StepRecord {
  index: number;
  thought: string;
  tool_calls: ToolCallRecord[];
  status: string; // pending | running | done | failed
}

export interface Artifact {
  id: string;
  filename: string;
  path: string;
  mime: string;
  size: number;
  created_at: string;
}

export interface Task {
  id: string;
  title: string;
  user_input: string;
  status: TaskStatus;
  mode: TaskMode;
  steps: StepRecord[];
  plan: PlanStep[];
  artifacts: Artifact[];
  /** User/assistant text turns across every round of the conversation. */
  conversation: ChatTurn[];
  final_answer: string;
  error?: string | null;
  created_at: string;
  updated_at: string;
  subtasks?: SubTask[]; // P1: sub-agent results
}

// --- P1 item 2: sub-agent ---

/** 子任务快照：面板只吃 REST 的 `Task.subtasks`（生产者由 #70-A 在 spawn 路径补回）；
 * 三条 `subtask_*` 汇总事件走父频道、只进 Trace 时间线，不喂本快照。 */
export interface SubTask {
  subtask_id: string;
  name: string;
  status: string; // pending | running | completed | failed
  summary: string;
  artifacts: string[];
  error?: string | null;
}

// --- P2 item 1: MCP server diagnostics (GET /api/mcp/servers) ---

export interface McpServerInfo {
  name: string;
  transport: string; // stdio (implemented) | http (reserved)
  status: "connected" | "error" | "disabled";
  tools_count: number;
  error?: string | null;
}

// --- P1 item 5: auth ---

export interface AuthTokenResponse {
  token: string;
  expires_at: string;
  ok: boolean;
}

export interface CreateTaskRequest {
  title?: string;
  input: string;
  mode?: TaskMode;
}

// --- P1-B: workspace rollback (POST /api/tasks/{id}/rollback) ---

/** Result of a whole-task rollback (docs/specs/p1-b-rollback.md). */
export interface RollbackResult {
  ok: boolean;
  /** Sandbox-relative paths that actually changed (empty = already original). */
  files: string[];
  already_original: boolean;
}

export interface ApiResponse<T = any> {
  code: number;
  data: T;
  message: string;
}

/**
 * SSE 事件词汇表。现役清单的唯一权威是 `docs/architecture.md` §3.4 的事件表，
 * 与 `hooks/useSSE.ts` 的订阅表 `EVENT_TYPES` 逐条相同（漏一条 = 前端静默收不到），
 * 由 `scripts/check-sse-event-registry.mjs` 机械断言。
 * `trace_end` 是唯一多出来的成员：`TraceRecorder.close()` 把它直写进 trace JSONL、
 * 从不进 EventBus，所以 trace 回放面要认它，而 SSE 订阅表里没有它。
 * `subtask_start` / `subtask_result` / `subtask_failed` 随 #70 裁定 A 回到词汇表
 * （#56 删入口 B 时退役，spawn 路径补回生产者后重发在父频道上）。
 */
export type SSEventType =
  | "task_created"
  | "plan_update"
  | "step_start"
  | "tool_call"
  | "tool_result"
  | "context_compressed" // P0: {step_index, dropped, band_evicted, converged, context_tokens, strategy}
  | "task_rollback" // P1-B: {task_id, ok, files, already_original}
  | "artifact_created"
  | "final_answer"
  | "task_failed"
  | "task_interrupted"
  | "task_resumed" // P3 / #139: {task_id, status}（续跑或多轮续说）
  | "tool_result_evicted" // T1.4: {tool_call_id, tool_name, original_chars, step_index}
  | "subtask_start" // #70-A: {subtask_id, name, status, parent_task_id}（父频道）
  | "subtask_result" // #70-A: {subtask_id, name, status, summary, artifacts}（父频道）
  | "subtask_failed" // #70-A: {subtask_id, name, error}（父频道）
  | "trace_end" // 仅 trace 回放面（见上方说明）
  | "heartbeat";

/**
 * One SSE frame. The wire carries only a string `type` and an unvalidated JSON
 * `data`, so the payload stays `any`: a full per-type discriminated union is
 * #104's line of work (publish-point AST enumeration). The confirm-face named
 * members that used to live here were retired with the gate in #138.
 */
export interface SSEvent {
  type: SSEventType;
  data: any;
  ts?: number;
}

// --- P1: trace replay / live markers ---

/** Payload of the ``context_compressed`` event. */
export interface ContextCompressedData {
  step_index: number;
  dropped: number;
  /** Absent on events persisted before the in-band squeeze (#49). */
  band_evicted?: number;
  /** False when the squeeze ran out of what it may give way and the context is still
   * oversized. Absent on events persisted before #49's A′ role gate. */
  converged?: boolean;
  context_tokens: number;
  strategy: string;
}

export type TraceMarkerType = "context_compressed";

/** Live marker rendered in the task flow (MessageStream). */
export interface TraceMarker {
  type: TraceMarkerType;
  ts?: number;
  data: ContextCompressedData;
}

/** Tabs shown on the task detail page. */
export type TaskViewTab = "run" | "trace" | "subtask";

/** #85：`compressed=true` 现在可在 `dropped=0` 时仅靠带内挤压成立——
 *  两个计数必须分开说，否则视图会显示「上下文已压缩：丢弃 0 条」而把真实工作（带内挤压）藏起来。 */
export function compressFaceParts(d: { dropped?: number; band_evicted?: number }): string {
  const parts: string[] = [];
  if (d.dropped) parts.push(`丢弃 ${d.dropped} 条早期消息`);
  if (d.band_evicted) parts.push(`带内 ${d.band_evicted} 条换成便签`);
  return parts.length ? parts.join("、") : "带内无可再挤";
}
