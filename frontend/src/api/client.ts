import {
  ApiResponse,
  Artifact,
  AuthTokenResponse,
  ConfirmRequest,
  CreateTaskRequest,
  KbDoc,
  SSEvent,
  Task,
} from "../types";

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

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

export const listTasks = (limit = 50) =>
  req<{ tasks: Task[] }>(`/tasks?limit=${limit}`);

export const getTask = (id: string) => req<Task>(`/tasks/${id}`);

export const stopTask = (id: string) =>
  req<{ ok: boolean; status: string }>(`/tasks/${id}/stop`, { method: "POST" });

export const confirmTask = (id: string, body: ConfirmRequest) =>
  req<{ ok: boolean }>(`/tasks/${id}/confirm`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// ── P1 item 5: auth ──
export const loginRequest = (secret: string) =>
  req<AuthTokenResponse>("/auth/token", {
    method: "POST",
    body: JSON.stringify({ token: secret }),
  });

export const logoutRequest = () => setAuthToken(null);

// ── P1 item 3: knowledge base management ──
export const listKbDocs = () => req<{ docs: KbDoc[] }>("/kb");

export const rebuildKb = () =>
  req<{ ok: boolean; indexed: number }>("/kb/rebuild", { method: "POST" });

export const deleteKbDoc = (docId: string) =>
  req<{ ok: boolean }>(`/kb/${encodeURIComponent(docId)}`, { method: "DELETE" });

const _withToken = (url: string): string => {
  const token = getAuthToken();
  return token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url;
};

export const artifactUrl = (id: string, aid: string) =>
  _withToken(`${API_BASE}/tasks/${id}/artifacts/${aid}`);

export const previewUrl = (id: string, aid: string) =>
  _withToken(`${API_BASE}/tasks/${id}/artifacts/${aid}/preview`);

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

export type { Artifact };
