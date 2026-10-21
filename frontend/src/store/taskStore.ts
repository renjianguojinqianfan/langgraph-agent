import { create } from "zustand";
import {
  SSEvent,
  StepRecord,
  Task,
  ToolCallRecord,
  TraceMarker,
} from "../types";

interface TaskState {
  tasks: Task[];
  currentTaskId: string | null;
  detail: Record<string, Task>;
  events: SSEvent[];
  /** Per-task live markers (context compressed) for the flow view. */
  markers: Record<string, TraceMarker[]>;
  selectedStep: number | null;
  /** History filter (ADR 0010 decision 2): this directory, or every task. */
  historyFilter: "current" | "all";
  /** Bumped by `task_created`; the history list refetches (membership is the server's). */
  listRevision: number;

  setTasks: (tasks: Task[]) => void;
  setCurrentTask: (id: string | null) => void;
  upsertTask: (task: Task) => void;
  applyEvent: (ev: SSEvent) => void;
  setSelectedStep: (index: number | null) => void;
  setHistoryFilter: (filter: "current" | "all") => void;
}

function makeStep(index: number): StepRecord {
  return { index, thought: "", tool_calls: [], status: "running" };
}

function emptyTask(id: string): Task {
  return {
    id,
    title: "",
    user_input: "",
    workspace: "",
    status: "PENDING",
    mode: "build",
    steps: [],
    plan: [],
    conversation: [],
    final_answer: "",
    error: null,
    created_at: "",
    updated_at: "",
    subtasks: [],
  };
}

function ensureStep(task: Task, index: number): Task {
  if (task.steps.some((s) => s.index === index)) return task;
  const steps = [...task.steps, makeStep(index)].sort((a, b) => a.index - b.index);
  return { ...task, steps };
}

/** Apply a single SSE event to a task snapshot. */
function applyToTask(task: Task, ev: SSEvent): Task {
  switch (ev.type) {
    case "task_created":
      return {
        ...task,
        id: ev.data.task_id ?? task.id,
        title: ev.data.title ?? task.title,
        status: ev.data.status ?? task.status,
      };
    case "plan_update":
      return { ...task, plan: ev.data.plan ?? [] };
    case "step_start": {
      const t = ensureStep(task, ev.data.index);
      const steps = t.steps.map((s) =>
        s.index === ev.data.index
          ? { ...s, thought: ev.data.thought ?? s.thought, status: "running" }
          : s
      );
      return { ...t, steps };
    }
    case "tool_call": {
      const rec = ev.data as ToolCallRecord;
      const steps = task.steps.length ? [...task.steps] : [makeStep(1)];
      const lastIdx = steps.length - 1;
      const last = steps[lastIdx];
      const exists = last.tool_calls.some((tc) => tc.id === rec.id);
      const tool_calls = exists
        ? last.tool_calls.map((tc) => (tc.id === rec.id ? { ...tc, ...rec } : tc))
        : [...last.tool_calls, rec];
      steps[lastIdx] = { ...last, tool_calls, status: "running" };
      return { ...task, steps };
    }
    case "tool_result": {
      const rec = ev.data as ToolCallRecord;
      const steps = task.steps.map((s) => {
        if (!s.tool_calls.some((tc) => tc.id === rec.id)) return s;
        return {
          ...s,
          tool_calls: s.tool_calls.map((tc) =>
            tc.id === rec.id
              ? {
                  ...tc,
                  output: rec.output,
                  status: rec.status,
                  error: rec.error,
                }
              : tc
          ),
        };
      });
      return { ...task, steps };
    }
    case "final_answer":
      // The conversation itself is refreshed from REST when the round parks
      // (see TaskView): appending here would duplicate every turn whenever the
      // SSE buffer is replayed on reconnect.
      return { ...task, final_answer: ev.data.answer ?? task.final_answer };
    case "task_failed":
      return { ...task, status: "FAILED", error: ev.data.error ?? task.error };
    case "task_interrupted":
      return { ...task, status: "INTERRUPTED" };
    case "task_resumed":
      // A continuation started (this tab or another one): the task is running.
      return { ...task, status: "RUNNING" };
    default:
      return task;
  }
}

export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
  currentTaskId: null,
  detail: {},
  events: [],
  markers: {},
  selectedStep: null,
  historyFilter: "current",
  listRevision: 0,

  setTasks: (tasks) => set({ tasks }),
  setHistoryFilter: (historyFilter) => set({ historyFilter }),

  setCurrentTask: (id) =>
    set({
      currentTaskId: id,
      selectedStep: null,
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

  applyEvent: (ev) =>
    set((state) => {
      const id: string | undefined = ev.data?.task_id ?? state.currentTaskId ?? undefined;
      if (!id) return {};
      const prev = state.detail[id] ?? emptyTask(id);
      const next = applyToTask(prev, ev);

      const existing = state.tasks.find((t) => t.id === id);
      const summary: Task = {
        ...emptyTask(id),
        ...next,
      };
      const tasks = existing
        ? state.tasks.map((t) => (t.id === id ? summary : t))
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

  setSelectedStep: (index) => set({ selectedStep: index }),
}));
