// Mirrors backend/api/schemas.py field names exactly.

export type TaskStatus =
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "INTERRUPTED";

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
  need_confirm: boolean;
  confirmed: boolean;
  confirm_outcome?: string; // #57: approved | denied | timed_out | aborted | auto_approved（"" = 未进闸门）
  circuit_open?: boolean; // P0: short-circuited by the circuit breaker
  retries?: number; // P0: retries performed by the tool executor
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
  steps: StepRecord[];
  plan: PlanStep[];
  artifacts: Artifact[];
  final_answer: string;
  error?: string | null;
  created_at: string;
  updated_at: string;
  risk_report?: RiskItem[]; // P1: risk scan report
  subtasks?: SubTask[]; // P1: sub-agent results
}

// --- P1 item 1: risk scan ---

export interface RiskItem {
  step_index: number;
  level: "none" | "low" | "medium" | "high";
  matched_keywords: string[];
  suggestion: string;
  action: "confirm" | "allow" | "block";
}

/** Payload of the ``risk_report`` event. */
export interface RiskReportData {
  items: RiskItem[];
  policy: string;
  semantic_enabled: boolean;
}

// --- P1 item 2: sub-agent ---

/** 子任务快照：只来自 REST 的 `Task.subtasks`；#56 起子任务没有独立事件流。 */
export interface SubTask {
  subtask_id: string;
  name: string;
  status: string; // pending | running | completed | failed
  summary: string;
  artifacts: string[];
  error?: string | null;
}

// --- P1 item 3: knowledge base ---

export interface KbDoc {
  doc_id: string;
  path: string;
  size: number;
  chunks: number;
  indexed_at: string;
}

export interface KbHit {
  doc_id: string;
  path: string;
  chunk_index: number;
  content: string;
  score: number;
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
}

export interface ConfirmRequest {
  tool_call_id: string;
  approved: boolean;
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
 * #56 起退役、后端零发布点的 `subtask_start` / `subtask_result` / `subtask_failed`
 * 已从本联合与订阅表删除（子任务的失败改由一条失败的 tool call 带回）。
 */
export type SSEventType =
  | "task_created"
  | "plan_update"
  | "step_start"
  | "tool_call"
  | "tool_result"
  | "tool_circuit_open" // P0: {tool_name, cooldown_sec}
  | "context_compressed" // P0: {step_index, dropped, band_evicted, converged, context_tokens, strategy}
  | "task_rollback" // P1-B: {task_id, ok, files, already_original}
  | "human_confirm_required"
  | "human_confirm_resolved" // #57: {tool_call_id, tool_name, outcome}（outcome 见 ToolCallRecord.confirm_outcome；入参不带，同 id 的 tool_call 事件已有且 trace 不脱敏）
  | "artifact_created"
  | "verification" // P0-B: {task_id, passed, failures, attempts, degraded, loop_back?}
  | "final_answer"
  | "task_completed"
  | "task_failed"
  | "task_interrupted"
  | "task_resumed" // P3: {task_id, status}
  | "tool_result_evicted" // T1.4: {tool_call_id, tool_name, original_chars, step_index}
  | "risk_report" // P1: {items, policy, semantic_enabled}
  | "risk_found" // P1: RiskItem
  | "trace_end" // 仅 trace 回放面（见上方说明）
  | "heartbeat";

export interface SSEvent {
  type: SSEventType;
  data: any;
  ts?: number;
}

export interface ConfirmDialogState {
  open: boolean;
  tool_call_id?: string;
  tool_name?: string;
  input?: any;
  task_id?: string;
}

// --- P1: trace replay / live markers ---

/** Payload of the ``tool_circuit_open`` event. */
export interface ToolCircuitOpenData {
  tool_name: string;
  cooldown_sec: number;
}

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

export type TraceMarkerType = "tool_circuit_open" | "context_compressed";

/** Live marker rendered in the task flow (MessageStream). */
export interface TraceMarker {
  type: TraceMarkerType;
  ts?: number;
  data: ToolCircuitOpenData | ContextCompressedData;
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
