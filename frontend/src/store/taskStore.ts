import { create } from "zustand";
import { SSEvent, Task, TraceMarker } from "../types";
import { applyToTask, mergeStatusSnapshot } from "./reduceTask";

interface TaskState {
  tasks: Task[];
  currentTaskId: string | null;
  detail: Record<string, Task>;
  events: SSEvent[];
  /** Per-task live markers (context compressed) for the flow view. */
  markers: Record<string, TraceMarker[]>;
  /** History filter (ADR 0010 decision 2): this directory, or every task. */
  historyFilter: "current" | "all";
  /** M2: the model settings overlay. */
  settingsOpen: boolean;
  /** Bumped by `task_created`; the history list refetches (membership is the server's). */
  listRevision: number;

  setTasks: (tasks: Task[]) => void;
  setSettingsOpen: (open: boolean) => void;
  setCurrentTask: (id: string | null) => void;
  upsertTask: (task: Task) => void;
  /** #189: merge a refetched snapshot's status fields into the live detail. */
  mergeTaskStatus: (snapshot: Task) => void;
  applyEvent: (ev: SSEvent) => void;
  setHistoryFilter: (filter: "current" | "all") => void;
}

export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
  currentTaskId: null,
  detail: {},
  events: [],
  markers: {},
  historyFilter: "current",
  settingsOpen: false,
  listRevision: 0,

  setTasks: (tasks) => set({ tasks }),
  setHistoryFilter: (historyFilter) => set({ historyFilter }),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),

  setCurrentTask: (id) => set({ currentTaskId: id }),

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
      const next = applyToTask(state.detail[id], id, ev);

      const existing = state.tasks.find((t) => t.id === id);
      const tasks = existing
        ? state.tasks.map((t) => (t.id === id ? next : t))
        : state.tasks;

      // Track flow markers (context compressed) per task so the message stream
      // can render them without losing cross-task isolation.
      let markers = state.markers;
      if (ev.type === "context_compressed") {
        const list = markers[id] ?? [];
        const marker: TraceMarker = { type: ev.type, ts: ev.ts, data: ev.data };
        markers = { ...markers, [id]: [...list, marker].slice(-200) };
      }

      return {
        detail: { ...state.detail, [id]: next },
        tasks,
        markers,
        events: [...state.events, ev].slice(-500),
        listRevision:
          ev.type === "task_created" ? state.listRevision + 1 : state.listRevision,
      };
    }),
}));
