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

export interface SubTask {
  subtask_id: string;
  name: string;
  status: string; // pending | running | completed | failed
  summary: string;
  artifacts: string[];
  error?: string | null;
}

export interface SubtaskStartData {
  subtask_id: string;
  name: string;
  status: string;
  parent_task_id: string;
}

export interface SubtaskResultData {
  subtask_id: string;
  name: string;
  status: string;
  summary: string;
  artifacts: string[];
}

export interface SubtaskFailedData {
  subtask_id: string;
  name: string;
  error: string;
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

export interface ApiResponse<T = any> {
  code: number;
  data: T;
  message: string;
}

export type SSEventType =
  | "task_created"
  | "plan_update"
  | "step_start"
  | "tool_call"
  | "tool_result"
  | "tool_circuit_open" // P0: {tool_name, cooldown_sec}
  | "context_compressed" // P0: {step_index, dropped, context_tokens, strategy}
  | "human_confirm_required"
  | "artifact_created"
  | "final_answer"
  | "task_completed"
  | "task_failed"
  | "task_interrupted"
  | "risk_report" // P1: {items, policy, semantic_enabled}
  | "risk_found" // P1: RiskItem
  | "subtask_start" // P1: {subtask_id, name, status, parent_task_id}
  | "subtask_result" // P1: {subtask_id, name, status, summary, artifacts}
  | "subtask_failed" // P1: {subtask_id, name, error}
  | "trace_end"
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
