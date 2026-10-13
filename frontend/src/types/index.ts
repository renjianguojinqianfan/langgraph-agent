// Mirrors backend/api/schemas.py field names exactly.

export type TaskStatus =
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "INTERRUPTED"
  | "UNVERIFIED"; // #46: 跑完并声称完成，但验收没通过（不是崩了，所以不并入 FAILED）

/**
 * 确认闸门的五档终态，与后端 `nodes.py` 的 `CONFIRM_*` 常量一一对应（#57 AC5/AC6）。
 * 空串 `""` 不是其中一档：它是「这条调用没进过闸门」，被单列在下面的类型里，
 * 以免把「没进闸门」误当成某一种失败（那正是 #57 之前折成「未批准即拒绝」的老坑）。
 */
export type ConfirmOutcome =
  | "approved"
  | "denied"
  | "timed_out"
  | "aborted"
  | "auto_approved";

/** 五档终态的中文文案表。显示面的唯一来源（StepDetail / TraceTab 都读这张，别造第二张）。 */
export const CONFIRM_OUTCOME_LABEL: Record<string, string> = {
  approved: "人已批准",
  denied: "人明确拒绝",
  timed_out: "超时未答",
  aborted: "被停止打断",
  auto_approved: "评测态自动放行",
};

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
  confirm_outcome?: ConfirmOutcome | ""; // #57: 五档终态；"" = 未进闸门（backend schemas.py 同源）
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
  verification?: Verification | null; // P0-B: 完成验证结论（#46 起 reason 取代 degraded）
  expectations?: Expectation[]; // #46: 调用方声明的合格线；空 = 信息型任务
  rubric?: string; // #46 第二步：语义合格线（后端 Task.rubric，"" = 未声明）。暂无消费者，
                   // 但 GET /api/tasks/{id} 会返回它——镜像契约面保持同形，别让字段静默差一格
  review?: TaskReview | null; // #46 切片 B：人工复核接受了这条 UNVERIFIED；null/缺席 = 没人推翻过
}

// --- #46: 声明式验收 ---

export interface Expectation {
  type: "artifact" | "tool_success";
  target: string; // artifact: 沙箱根相对路径；tool_success: 工具名或 "*"
}

export interface Verification {
  passed: boolean;
  failures: string[];
  attempts: number;
  // "" 通过 | pending 回环中 | retry_exhausted 预算耗尽 | max_steps 被步数抢占
  // | grader_error 裁判自身出错（#46 第二步）
  reason: string;
  loop_back?: boolean;
}

export interface TaskReview {
  // 后端 Task.review（#46 切片 B）：一次「人工把 UNVERIFIED 接受为 COMPLETED」的留痕。
  // 它是 verification 的**兄弟字段**而不是其成员——机器的判决一字不改（passed 仍是 false）。
  at: string;
  reviewer: string; // 自报的复核人名：本仓只有 token 鉴权，没有账号，身份强度到此为止
  note: string;
  overruled_reason: string; // 被推翻的那条 verification.reason
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
 * `subtask_start` / `subtask_result` / `subtask_failed` 随 #70 裁定 A 回到词汇表
 * （#56 删入口 B 时退役，spawn 路径补回生产者后重发在父频道上）。
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
  | "verification" // P0-B: {task_id, passed, failures, attempts, reason, loop_back?}（#46：reason 取代 degraded）
  | "final_answer"
  | "task_completed"
  | "task_failed"
  | "task_interrupted"
  | "task_unverified" // #46: {task_id, status, reason} —— 跑完但验收没过，与 interrupted 同构
  | "task_accepted" // #46 切片 B: {task_id, status, reason, reviewer, note} —— 人工复核接受了那条 UNVERIFIED
  | "task_resumed" // P3: {task_id, status}
  | "tool_result_evicted" // T1.4: {tool_call_id, tool_name, original_chars, step_index}
  | "risk_report" // P1: {items, policy, semantic_enabled}
  | "risk_found" // P1: RiskItem
  | "subtask_start" // #70-A: {subtask_id, name, status, parent_task_id}（父频道）
  | "subtask_result" // #70-A: {subtask_id, name, status, summary, artifacts}（父频道）
  | "subtask_failed" // #70-A: {subtask_id, name, error}（父频道）
  | "trace_end" // 仅 trace 回放面（见上方说明）
  | "heartbeat";

/**
 * 确认面两个事件的具名成员（#91 AC3 返工）。
 *
 * `SSEvent` 过去是 `{ type: SSEventType; data: any }`——`data` 永远是 `any`，所谓「按 type
 * 收窄」在编译期不存在：后端把字段名改掉，`tsc` 一声不响（正是票面第 3 条要治的病）。
 * 现在按 `type` 可判别，消费侧拿到的是具名载荷。
 */
export interface HumanConfirmRequiredEvent {
  type: "human_confirm_required";
  data: HumanConfirmRequiredData;
  ts?: number;
}

export interface HumanConfirmResolvedEvent {
  type: "human_confirm_resolved";
  data: HumanConfirmResolvedData;
  ts?: number;
}

/** 除确认面两个之外的事件类型（含只进联合、不进订阅表的 `trace_end` 与无载荷的 `heartbeat`）。 */
export type OtherSSEventType = Exclude<
  SSEventType,
  "human_confirm_required" | "human_confirm_resolved"
>;

/**
 * 其余事件的宽松成员：载荷仍是 `any`。范围就到票面要的「至少覆盖确认面两个事件」为止，
 * 全事件判别联合是 #104 那条线（发布点 AST 枚举）落地后的事，不在本票扩面。
 */
export interface LooseSSEvent {
  type: OtherSSEventType;
  data: any;
  ts?: number;
}

export type SSEvent =
  | HumanConfirmRequiredEvent
  | HumanConfirmResolvedEvent
  | LooseSSEvent;

export interface ConfirmDialogState {
  open: boolean;
  tool_call_id?: string;
  tool_name?: string;
  input?: any;
  task_id?: string;
}

// --- #91 AC3: 确认面事件载荷的类型接口 ---
// 参照 `ContextCompressedData` 的先例：给「在发」的 confirm 事件载荷建命名类型，
// 让 store 消费侧有编译期落点，后端改字段名时不至于全靠人肉对齐。这里只收口确认
// 相关的两个事件 + tool_result 上的 `confirm_outcome`（已并入 `ToolCallRecord`），
// 不做全事件判别联合——那是另一张票的体量（`docs/architecture.md` §3.4 全表逐条建模）。

/** Payload of the ``human_confirm_required`` event（nodes.py `_ask_human` / risk_plan 两处发布）。 */
export interface HumanConfirmRequiredData {
  tool_call_id: string;
  tool_name: string;
  input: Record<string, any>;
}

/** Payload of the ``human_confirm_resolved`` event（#57 AC5 五档终态）。 */
export interface HumanConfirmResolvedData {
  tool_call_id: string;
  tool_name: string;
  outcome: ConfirmOutcome;
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
