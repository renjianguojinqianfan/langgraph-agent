import { create } from "zustand";
import { SSEvent, Task, TraceMarker } from "../types";
import { currentDir, setDirParam } from "../workspace";
import { applyToTask, mergeSnapshotUnlessStale, mergeStatusSnapshot } from "./reduceTask";

interface TaskState {
  tasks: Task[];
  currentTaskId: string | null;
  detail: Record<string, Task>;
  /** Per-task SSE events for the event pane (memory only; lost on reload). */
  eventsByTask: Record<string, SSEvent[]>;
  /** Per-task live markers (context compressed) for the flow view. */
  markers: Record<string, TraceMarker[]>;
  /** History filter (ADR 0010 decision 2): this directory, or every task. */
  historyFilter: "current" | "all";
  /** Page's workspace (`?dir=`); empty means the backend's launch directory. */
  workspaceDir: string;
  /** M2: the model settings overlay. */
  settingsOpen: boolean;
  /** Bumped by `task_created`; the history list refetches (membership is the server's). */
  listRevision: number;
  /** Non-empty when the last history load failed (backend unreachable). */
  listError: string;
  /** #256: true while the history list is in flight — the empty copy waits. */
  listLoading: boolean;
  /** #256: per-task snapshot load failure; a set entry is never a new task. */
  detailError: Record<string, string>;
  /** #256: bumped by `retryDetail`; the snapshot effect keys on it. */
  detailRevision: number;
  /**
   * #256: which face last answered 401. Per-source on purpose: the list,
   * detail and plan reads happen independently, and one global flag let a
   * 401 on one face put a "重新登录" button on another face's transport
   * failure (复审 LOW). Each effect writes — and each face reads — only its
   * own entry, so the copy and the button come from the same answer.
   */
  authExpired: Record<"list" | "detail" | "plan", boolean>;

  setTasks: (tasks: Task[]) => void;
  setSettingsOpen: (open: boolean) => void;
  setCurrentTask: (id: string | null) => void;
  /** #241: drop a deleted task from lists/detail/caches. */
  removeTask: (id: string) => void;
  /** #241: drop every task of one workspace after a directory delete. */
  removeWorkspaceTasks: (workspace: string) => void;
  /** #242: update URL + history/creation target; not a backend restart. */
  switchWorkspace: (dir: string) => void;
  upsertTask: (task: Task) => void;
  /** #189: merge a refetched snapshot's status fields into the live detail. */
  mergeTaskStatus: (snapshot: Task) => void;
  applyEvent: (ev: SSEvent) => void;
  setHistoryFilter: (filter: "current" | "all") => void;
  setListError: (message: string) => void;
  setListLoading: (loading: boolean) => void;
  /** #256: refetch the history list (the load effect keys on `listRevision`). */
  retryList: () => void;
  /** #256: remember (or clear) why a task's snapshot could not be loaded. */
  setDetailError: (id: string, message: string) => void;
  /** #256: refetch the selected task's snapshot (the effect keys on the revision). */
  retryDetail: () => void;
  /** #256: mirror a saved plan version onto the task so PlanAnchor can't go stale. */
  setPlanVersion: (id: string, version: number) => void;
  /** #256: a 401 answered on one face; that face offers re-login while set. */
  setAuthExpired: (source: "list" | "detail" | "plan", expired: boolean) => void;
}

/** Remove ids from every per-task cache; clear the selection if it was removed. */
function dropTasks(state: TaskState, ids: Set<string>): Partial<TaskState> {
  const detail = { ...state.detail };
  const eventsByTask = { ...state.eventsByTask };
  const markers = { ...state.markers };
  const detailError = { ...state.detailError };
  for (const id of ids) {
    delete detail[id];
    delete eventsByTask[id];
    delete markers[id];
    delete detailError[id];
  }
  return {
    tasks: state.tasks.filter((task) => !ids.has(task.id)),
    detail,
    eventsByTask,
    markers,
    detailError,
    currentTaskId:
      state.currentTaskId && ids.has(state.currentTaskId)
        ? null
        : state.currentTaskId,
    listRevision: state.listRevision + 1,
  };
}

export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
  currentTaskId: null,
  detail: {},
  eventsByTask: {},
  markers: {},
  historyFilter: "current",
  workspaceDir: currentDir() ?? "",
  settingsOpen: false,
  listRevision: 0,
  listError: "",
  // #256: starts true — the first list answer has not arrived, and the empty
  // copy waiting for it is the AC7 flash (effect-set true lands one frame late).
  listLoading: true,
  detailError: {},
  detailRevision: 0,
  authExpired: { list: false, detail: false, plan: false },

  setTasks: (tasks) => set({ tasks }),
  setHistoryFilter: (historyFilter) => set({ historyFilter }),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  setListError: (listError) => set({ listError }),
  setListLoading: (listLoading) => set({ listLoading }),
  retryList: () => set((state) => ({ listRevision: state.listRevision + 1 })),
  setDetailError: (id, message) =>
    set((state) => {
      const detailError = { ...state.detailError };
      if (message) detailError[id] = message;
      else delete detailError[id];
      return { detailError };
    }),
  retryDetail: () => set((state) => ({ detailRevision: state.detailRevision + 1 })),
  setAuthExpired: (source, expired) =>
    set((state) =>
      state.authExpired[source] === expired
        ? {}
        : { authExpired: { ...state.authExpired, [source]: expired } }
    ),
  setPlanVersion: (id, version) =>
    set((state) => {
      const prev = state.detail[id];
      if (!prev || prev.plan_version === version) return {};
      const next = { ...prev, plan_version: version };
      return {
        detail: { ...state.detail, [id]: next },
        tasks: state.tasks.map((t) => (t.id === id ? next : t)),
      };
    }),

  setCurrentTask: (id) => set({ currentTaskId: id }),

  removeTask: (id) => set((state) => dropTasks(state, new Set([id]))),

  removeWorkspaceTasks: (workspace) =>
    set((state) => {
      // Also scan `detail`: a selected task can slide out of the 50-row list
      // while its snapshot (and selection) stays in memory, and it must not
      // survive its directory's delete (评审 MEDIUM, #251).
      const ids = new Set<string>();
      for (const task of state.tasks) {
        if (task.workspace === workspace) ids.add(task.id);
      }
      for (const task of Object.values(state.detail)) {
        if (task.workspace === workspace) ids.add(task.id);
      }
      return dropTasks(state, ids);
    }),

  switchWorkspace: (workspaceDir) => {
    setDirParam(workspaceDir);
    set((state) => ({
      workspaceDir,
      // The old selection belongs to the old directory's history.
      currentTaskId: null,
      listRevision: state.listRevision + 1,
    }));
  },

  upsertTask: (task) =>
    set((state) => {
      // #256: a late snapshot (requested before the stream caught up) must not
      // replace the detail wholesale — mergeSnapshotUnlessStale keeps the live
      // steps (and the plan version a save synced onto the task).
      const prev = state.detail[task.id];
      const next = prev ? mergeSnapshotUnlessStale(prev, task) : task;
      const detail = { ...state.detail, [task.id]: next };
      // Membership belongs to the server's filtered GET: a task from another
      // directory must not be inserted by a detail fetch or SSE event.
      if (!state.tasks.some((t) => t.id === task.id)) return { detail };
      return {
        detail,
        tasks: state.tasks.map((t) => (t.id === task.id ? next : t)),
      };
    }),

  mergeTaskStatus: (snapshot) =>
    set((state) => {
      const prev = state.detail[snapshot.id];
      // No snapshot yet: nothing live to preserve.
      const merged = prev ? mergeStatusSnapshot(prev, snapshot) : snapshot;
      return {
        detail: { ...state.detail, [snapshot.id]: merged },
        tasks: state.tasks.map((t) => (t.id === snapshot.id ? merged : t)),
      };
    }),

  applyEvent: (ev) =>
    set((state) => {
      const id: string | undefined = ev.data?.task_id ?? state.currentTaskId ?? undefined;
      if (!id) return {};
      // `applyToTask` ignores a replayed `task_created` once a snapshot exists
      // (#189): the persisted snapshot, not event replay, is authoritative.
      // A list row is a full Task snapshot too, so it seeds a missing detail
      // instead of letting the stale replay replace the row.
      const known = state.tasks.find((t) => t.id === id);
      const next = applyToTask(state.detail[id] ?? known, id, ev);

      const existing = state.tasks.find((t) => t.id === id);
      const tasks = existing
        ? state.tasks.map((t) => (t.id === id ? next : t))
        : state.tasks;

      // Track flow markers (context compressed) per task so the message stream
      // can render them without losing cross-task isolation. Reconnect replays
      // the same frame; keep each payload once (latest arrival wins).
      let markers = state.markers;
      if (ev.type === "context_compressed") {
        const marker: TraceMarker = { type: ev.type, ts: ev.ts, data: ev.data };
        const key = `${marker.type}:${JSON.stringify(marker.data)}`;
        const kept = (markers[id] ?? []).filter(
          (m) => `${m.type}:${JSON.stringify(m.data)}` !== key
        );
        markers = { ...markers, [id]: [...kept, marker].slice(-200) };
      }

      const events = state.eventsByTask[id] ?? [];

      return {
        detail: { ...state.detail, [id]: next },
        tasks,
        markers,
        eventsByTask: {
          ...state.eventsByTask,
          [id]: [...events, ev].slice(-500),
        },
        listRevision:
          ev.type === "task_created" ? state.listRevision + 1 : state.listRevision,
      };
    }),
}));
