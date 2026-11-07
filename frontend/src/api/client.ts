import {
  ApiResponse,
  AuthTokenResponse,
  ChatTurn,
  CreateTaskRequest,
  DiscoverCandidate,
  FilePreview,
  ModelEntry,
  ModelKeySource,
  ModelProfile,
  ModelsData,
  Provider,
  ProviderCreateRequest,
  ProvidersData,
  ProviderUpdateRequest,
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

/** One status-preserving answer; `data` is null unless `parse` accepted it. */
interface StatusResult<T> {
  /** HTTP status; 0 means the request never reached the backend. */
  status: number;
  data: T | null;
  /** FastAPI `detail` (or envelope message) for the failure copy. */
  detail: string;
}

/**
 * #256: the one status-preserving read behind every snapshot face (list /
 * task / plan / login, and `previewFile` behind it). `req()` flattens every
 * answer into a body, so a non-2xx with a JSON body — a 500 from a
 * persistence failure, a 401 from an expired token — looked like an empty
 * envelope and the callers fell back to their empty faces.
 *
 * Never rejects: a transport failure comes back as `status: 0`, and `parse`
 * decides whether the envelope carries a usable payload. The failure copy
 * comes from `status` / `detail`, never from a rejection.
 */
async function reqWithStatus<T>(
  path: string,
  parse: (json: unknown) => T | null,
  init?: RequestInit
): Promise<StatusResult<T>> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    return { status: 0, data: null, detail: "" };
  }
  const json = await res.json().catch(() => null);
  return { status: res.status, data: parse(json), detail: json ? detailText(json) : "" };
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

export type TaskListSnapshotResult = StatusResult<{ tasks: Task[] }>;

/**
 * #256: list tasks keeping the HTTP status. A JSON-bodied 500 (persistence
 * failure) or 401 (expired token) must reach the error face, not the empty
 * list — the #256 bug on the list path.
 */
export const listTasksSnapshot = (limit = 50, dir = "current") =>
  reqWithStatus<{ tasks: Task[] }>(
    `/tasks?limit=${limit}&dir=${encodeURIComponent(dir)}`,
    (json) => {
      const tasks = (json as { data?: { tasks?: unknown } } | null)?.data?.tasks;
      return Array.isArray(tasks) ? { tasks: tasks as Task[] } : null;
    }
  );

/** #242: canonicalize + existence-check a directory before switching to it. */
export const validateWorkspace = (path: string) =>
  req<{ path: string }>(`/workspaces/validate?path=${encodeURIComponent(path)}`);

export const getTask = (id: string) => req<Task>(`/tasks/${id}`);

export type TaskSnapshotResult = StatusResult<Task>;

/**
 * #256: fetch one task's snapshot keeping the HTTP status. A 404 ("task not
 * found") used to look like an empty response and the center column fell
 * back to the "新任务" face.
 */
export const getTaskSnapshot = (id: string) =>
  reqWithStatus<Task>(`/tasks/${encodeURIComponent(id)}`, (json) => {
    const data = (json as { data?: { id?: unknown } } | null)?.data;
    return data && typeof data.id === "string" ? (data as Task) : null;
  });

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

export type PlanSnapshotResult = StatusResult<{ text: string; version: number }>;

/**
 * #256: read the plan keeping the HTTP status. A failed read must not fall
 * back to the empty-plan face — that face is what let an empty `PUT`
 * overwrite the real file on disk (`backend/api/routes.py`: 404 task gone,
 * 409 plan file not UTF-8).
 */
export const getPlanSnapshot = (id: string) =>
  reqWithStatus<{ text: string; version: number }>(
    `/tasks/${encodeURIComponent(id)}/plan`,
    (json) => {
      const data = (json as { data?: { text?: unknown; version?: unknown } } | null)?.data;
      return data && typeof data.text === "string" && typeof data.version === "number"
        ? { text: data.text, version: data.version }
        : null;
    }
  );

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

/** Switch a task's model (D-34). `{model}` names a catalog entry; adding
 * `provider` names a raw model under that provider and the entry is created on
 * demand (source: manual) — that is the catalog-outside path. */
export const putTaskModel = (
  id: string,
  body: { model: string; provider?: string }
) =>
  req<{ ok: boolean; model: string }>(`/tasks/${id}/model`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

// ── #243 P2-2/P2-3: providers + model catalog (D-34) ──
// The legacy /api/models surface above stays the flat compatibility projection
// (listModels is still the single read behind the Composer's id → label map);
// these are the v2 write faces the settings page drives.
export const listProviders = () => req<ProvidersData>("/providers");

export const createProvider = (body: ProviderCreateRequest) =>
  req<Provider>("/providers", { method: "POST", body: JSON.stringify(body) });

export const updateProvider = (id: string, body: ProviderUpdateRequest) =>
  req<Provider>(`/providers/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const deleteProvider = (id: string) =>
  req<{ ok: boolean }>(`/providers/${encodeURIComponent(id)}`, { method: "DELETE" });

export const addProviderModel = (id: string, body: { model: string; name?: string }) =>
  req<ModelEntry>(`/providers/${encodeURIComponent(id)}/models`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const deleteProviderModel = (id: string, modelId: string) =>
  req<{ ok: boolean }>(
    `/providers/${encodeURIComponent(id)}/models/${encodeURIComponent(modelId)}`,
    { method: "DELETE" }
  );

/** Preview the provider's upstream `/models` list; never persisted. The
 * service bounds time / response size and refuses redirects (D-34). */
export const discoverProviderModels = (id: string) =>
  req<{ models: DiscoverCandidate[] }>(
    `/providers/${encodeURIComponent(id)}/models/discover`,
    { method: "POST" }
  );

// ── P1 item 5: auth ──
export type LoginAttempt = StatusResult<AuthTokenResponse>;

/**
 * #256: post the secret keeping the HTTP status. `req()` flattens a 401
 * ("invalid token") into a body, which made a wrong secret and a dead
 * backend look identical to the login page.
 */
export const loginAttempt = (secret: string) =>
  reqWithStatus<AuthTokenResponse>(
    "/auth/token",
    (json) => {
      const data = (json as { data?: { token?: unknown } } | null)?.data;
      return data && typeof data.token === "string" ? (data as AuthTokenResponse) : null;
    },
    { method: "POST", body: JSON.stringify({ token: secret }) }
  );

export const logoutRequest = () => setAuthToken(null);

const _withToken = (url: string): string => {
  const token = getAuthToken();
  return token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url;
};

// ── C5: task workspace files ──
// D-31 / #258 AC5: keep the HTTP status so FilePane can name the cause
// (404 task gone vs 409 workspace gone vs 401 dead token …), like previewFile.
export const listFiles = (id: string) =>
  reqWithStatus<{ files: WorkspaceFile[]; truncated: boolean }>(
    `/tasks/${encodeURIComponent(id)}/files`,
    (json) => {
      const data = (json as { data?: { files?: unknown; truncated?: unknown } } | null)
        ?.data;
      return data && Array.isArray(data.files)
        ? { files: data.files as WorkspaceFile[], truncated: !!data.truncated }
        : null;
    }
  );

export type PreviewResult = StatusResult<FilePreview>;

/**
 * Preview one file, keeping the HTTP status: #266 needs 415 / 400 / 404 / 409
 * to read differently, and `req()` flattens them into a body without a status.
 */
export const previewFile = (id: string, path: string) =>
  reqWithStatus<FilePreview>(
    `/tasks/${id}/files/preview?path=${encodeURIComponent(path)}`,
    (json) => {
      const data = (json as { data?: { content?: unknown } } | null)?.data;
      return data && typeof data.content === "string" ? (data as FilePreview) : null;
    }
  );

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

