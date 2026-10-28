import { create } from "zustand";
import { SSEvent, Task, TraceMarker } from "../types";
import { applyToTask, mergeStatusSnapshot } from "./reduceTask";

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
  /** M2: the model settings overlay. */
  settingsOpen: boolean;
  /** Bumped by `task_created`; the history list refetches (membership is the server's). */
  listRevision: number;
  /** Non-empty when the last history load failed (backend unreachable). */
  listError: string;

  setTasks: (tasks: Task[]) => void;
  setSettingsOpen: (open: boolean) => void;
  setCurrentTask: (id: string | null) => void;
  /** #241: drop a deleted task from lists/detail/caches. */
  removeTask: (id: string) => void;
  /** #241: drop every task of one workspace after a directory delete. */
  removeWorkspaceTasks: (workspace: string) => void;
  upsertTask: (task: Task) => void;
  /** #189: merge a refetched snapshot's status fields into the live detail. */
  mergeTaskStatus: (snapshot: Task) => void;
  applyEvent: (ev: SSEvent) => void;
  setHistoryFilter: (filter: "current" | "all") => void;
  setListError: (message: string) => void;
}

/** Remove ids from every per-task cache; clear the selection if it was removed. */
function dropTasks(state: TaskState, ids: Set<string>): Partial<TaskState> {
  const detail = { ...state.detail };
  const eventsByTask = { ...state.eventsByTask };
  const markers = { ...state.markers };
  for (const id of ids) {
    delete detail[id];
    delete eventsByTask[id];
    delete markers[id];
  }
  return {
    tasks: state.tasks.filter((task) => !ids.has(task.id)),
    detail,
    eventsByTask,
    markers,
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
  settingsOpen: false,
  listRevision: 0,
  listError: "",

  setTasks: (tasks) => set({ tasks }),
  setHistoryFilter: (historyFilter) => set({ historyFilter }),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  setListError: (listError) => set({ listError }),

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

  upsertTask: (task) =>
    set((state) => {
      const detail = { ...state.detail, [task.id]: task };
      // Membership belongs to the server's filtered GET: a task from another
      // directory must not be inserted by a detail fetch or SSE event.
      if (!state.tasks.some((t) => t.id === task.id)) return { detail };
      return {
        detail,
        tasks: state.tasks.map((t) => (t.id === task.id ? task : t)),
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
