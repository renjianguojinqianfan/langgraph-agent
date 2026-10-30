import {
  ApiResponse,
  AuthTokenResponse,
  ChatTurn,
  CreateTaskRequest,
  FilePreview,
  ModelKeySource,
  ModelProfile,
  ModelsData,
  SSEvent,
  Task,
  TaskMode,
  WorkspaceFile,
} from "../types";

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

/** Failure text from either a FastAPI ``{detail}`` body or a non-ok envelope. */
function detailText(r: unknown): string {
  const detail = (r as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  const message = (r as { message?: unknown })?.message;
  return typeof message === "string" && message !== "ok" ? message : "";
}

export function apiErrorText(r: unknown): string {
  return detailText(r) || "操作失败";
}

// localStorage key must match backend/services/auth.py:TOKEN_STORAGE_KEY.
const TOKEN_KEY = "lga_auth_token";

export function getAuthToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAuthToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    ...init,
  });
  const json = (await res.json()) as ApiResponse<T>;
  return json;
}

/** SSE URL for a task; appends the bearer token as ``?token=`` when present
 * (EventSource cannot set custom headers). */
export function eventsUrl(taskId: string): string {
  const token = getAuthToken();
  const base = `${API_BASE}/tasks/${encodeURIComponent(taskId)}/events`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

/** Health endpoint lives at the API root (not under /api). */
export function healthUrl(): string {
  const root = API_BASE.endsWith("/api") ? API_BASE.slice(0, -4) : API_BASE;
  return `${root}/health`;
}

export const createTask = (body: CreateTaskRequest) =>
  req<{ task_id: string }>("/tasks", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const listTasks = (limit = 50, dir = "current") =>
  req<{ tasks: Task[] }>(`/tasks?limit=${limit}&dir=${encodeURIComponent(dir)}`);

/** #242: canonicalize + existence-check a directory before switching to it. */
export const validateWorkspace = (path: string) =>
  req<{ path: string }>(`/workspaces/validate?path=${encodeURIComponent(path)}`);

export const getTask = (id: string) => req<Task>(`/tasks/${id}`);

/** #241: delete one task's history (record + checkpoint + trace; disk stays). */
export const deleteTask = (id: string) =>
  req<{ ok: boolean; cleanup: { checkpoint: boolean; trace: boolean } }>(
    `/tasks/${id}`,
    { method: "DELETE" }
  );

/** #241: delete one directory's history; `dir` is required (no "all"). */
export const deleteWorkspaceTasks = (dir: string) =>
  req<{ deleted: number }>(`/tasks?dir=${encodeURIComponent(dir)}`, {
    method: "DELETE",
  });

export const stopTask = (id: string) =>
  req<{ ok: boolean; status: string }>(`/tasks/${id}/stop`, { method: "POST" });

// ── #139: multi-turn conversation, plan/build modes, editable plan ──
export const putMode = (id: string, mode: TaskMode) =>
  req<{ ok: boolean; mode: TaskMode }>(`/tasks/${id}/mode`, {
    method: "PUT",
    body: JSON.stringify({ mode }),
  });

/** Replace a parked task's plan; it seeds the next round. */
export const getPlan = (id: string) =>
  req<{ text: string; version: number }>(`/tasks/${id}/plan`);

export const putPlan = (id: string, text: string) =>
  req<{ ok: boolean; version: number }>(`/tasks/${id}/plan`, {
    method: "PUT",
    body: JSON.stringify({ text }),
  });

/** Say one more thing to a parked task and continue the same conversation. */
export const sendMessage = (id: string, input: string) =>
  req<{ ok: boolean; status: string; task: Task }>(`/tasks/${id}/messages`, {
    method: "POST",
    body: JSON.stringify({ input }),
  });

export type { ChatTurn };

// ── M1/M2: model profiles (Key is write-only) ──
export const listModels = () => req<ModelsData>("/models");

export const createModel = (body: {
  name: string;
  base_url: string;
  model: string;
  api_key_env: string;
}) => req<ModelProfile>("/models", { method: "POST", body: JSON.stringify(body) });

export const updateModel = (
  id: string,
  body: Partial<Pick<ModelProfile, "name" | "base_url" | "model" | "api_key_env">>
) => req<ModelProfile>(`/models/${encodeURIComponent(id)}`, {
  method: "PUT",
  body: JSON.stringify(body),
});

export const deleteModel = (id: string) =>
  req<{ ok: boolean }>(`/models/${encodeURIComponent(id)}`, { method: "DELETE" });

export const setDefaultModel = (id: string) =>
  req<{ ok: boolean; default: string }>(`/models/${encodeURIComponent(id)}/default`, {
    method: "POST",
  });

/** Write-only: the response reports set/source, never the key. */
export const setModelKey = (id: string, key: string) =>
  req<{ ok: boolean; key_set: boolean; key_source: ModelKeySource }>(
    `/models/${encodeURIComponent(id)}/key`,
    { method: "PUT", body: JSON.stringify({ key }) }
  );

export const testModel = (id: string) =>
  req<{ ok: boolean; error?: string; note?: string }>(
    `/models/${encodeURIComponent(id)}/test`,
    { method: "POST" }
  );

export const putTaskModel = (id: string, model: string) =>
  req<{ ok: boolean; model: string }>(`/tasks/${id}/model`, {
    method: "PUT",
    body: JSON.stringify({ model }),
  });

// ── P1 item 5: auth ──
export const loginRequest = (secret: string) =>
  req<AuthTokenResponse>("/auth/token", {
    method: "POST",
    body: JSON.stringify({ token: secret }),
  });

export const logoutRequest = () => setAuthToken(null);

const _withToken = (url: string): string => {
  const token = getAuthToken();
  return token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url;
};

// ── C5: task workspace files ──
export const listFiles = (id: string) =>
  req<{ files: WorkspaceFile[]; truncated: boolean }>(`/tasks/${id}/files`);

export interface PreviewResult {
  /** HTTP status; 0 means the request never reached the backend. */
  status: number;
  data: FilePreview | null;
  /** FastAPI `detail` (or envelope message) for the failure copy. */
  detail: string;
}

/**
 * Preview one file, keeping the HTTP status: #266 needs 415 / 400 / 404 / 409
 * to read differently, and `req()` flattens them into a body without a status.
 */
export async function previewFile(id: string, path: string): Promise<PreviewResult> {
  const token = getAuthToken();
  let res: Response;
  try {
    res = await fetch(
      `${API_BASE}/tasks/${id}/files/preview?path=${encodeURIComponent(path)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : undefined }
    );
  } catch {
    return { status: 0, data: null, detail: "" };
  }
  const json = (await res.json().catch(() => null)) as
    | { data?: FilePreview; detail?: unknown; message?: unknown }
    | null;
  const data = json?.data;
  return {
    status: res.status,
    data: data && typeof data.content === "string" ? data : null,
    detail: json ? detailText(json) : "",
  };
}

export const fileDownloadUrl = (id: string, path: string) =>
  _withToken(`${API_BASE}/tasks/${id}/files/download?path=${encodeURIComponent(path)}`);

/** Raised when the trace endpoint is unreachable / the file is missing. */
export class TraceFetchError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "TraceFetchError";
    this.status = status;
  }
}

export interface TraceResult {
  events: SSEvent[];
  raw: string;
}

/**
 * Fetch a task's persisted trace.
 *
 * The backend's default response for ``GET /tasks/{id}/trace`` is raw
 * ``application/x-ndjson`` text — one JSON event per line, byte-for-byte the
 * on-disk trace file. We request that default shape, parse each line into an
 * event for the timeline/filter views, and keep the original text untouched
 * in ``raw`` so exports are identical to the backend file (no re-serialization
 * drift on float ``ts`` precision, escaping, or malformed lines).
 *
 * Throws :class:`TraceFetchError` with the HTTP status on failure (e.g. 404
 * when trace is disabled or the file is missing).
 */
export async function getTaskTrace(taskId: string): Promise<TraceResult> {
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(
    `${API_BASE}/tasks/${encodeURIComponent(taskId)}/trace`,
    { headers }
  );
  const text = await res.text();

  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const j = JSON.parse(text) as { detail?: string };
      if (j?.detail) message = String(j.detail);
    } catch {
      /* non-JSON error body, keep the HTTP fallback message */
    }
    throw new TraceFetchError(res.status, message);
  }

  const events: SSEvent[] = [];
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const ev = JSON.parse(trimmed) as SSEvent;
      if (ev && typeof ev.type === "string") events.push(ev);
    } catch {
      /* skip malformed lines so one bad line cannot break the replay */
    }
  }
  return { events, raw: text };
}

