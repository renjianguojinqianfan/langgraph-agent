import { create } from "zustand";
import {
  Artifact,
  ConfirmDialogState,
  RiskItem,
  SSEvent,
  StepRecord,
  SubTask,
  Task,
  ToolCallRecord,
  TraceMarker,
} from "../types";

interface TaskState {
  tasks: Task[];
  currentTaskId: string | null;
  detail: Record<string, Task>;
  events: SSEvent[];
  /** Per-task live markers (circuit open / context compressed) for the flow view. */
  markers: Record<string, TraceMarker[]>;
  confirm: ConfirmDialogState;
  selectedStep: number | null;

  setTasks: (tasks: Task[]) => void;
  setCurrentTask: (id: string | null) => void;
  upsertTask: (task: Task) => void;
  applyEvent: (ev: SSEvent) => void;
  openConfirm: (c: Omit<ConfirmDialogState, "open">) => void;
  closeConfirm: () => void;
  setSelectedStep: (index: number | null) => void;
}

function makeStep(index: number): StepRecord {
  return { index, thought: "", tool_calls: [], status: "running" };
}

function emptyTask(id: string): Task {
  return {
    id,
    title: "",
    user_input: "",
    status: "PENDING",
    steps: [],
    plan: [],
    artifacts: [],
    final_answer: "",
    error: null,
    created_at: "",
    updated_at: "",
    risk_report: [],
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
                  // The backend attaches these flags on the tool_result event.
                  circuit_open: rec.circuit_open ?? tc.circuit_open,
                  retries: rec.retries ?? tc.retries,
                }
              : tc
          ),
        };
      });
      return { ...task, steps };
    }
    case "human_confirm_required": {
      const tcId = ev.data.tool_call_id as string;
      const steps = task.steps.map((s) => ({
        ...s,
        tool_calls: s.tool_calls.map((tc) =>
          tc.id === tcId ? { ...tc, need_confirm: true } : tc
        ),
      }));
      return { ...task, steps };
    }
    case "artifact_created": {
      const art = ev.data as Artifact;
      if (task.artifacts.some((a) => a.id === art.id)) return task;
      return { ...task, artifacts: [...task.artifacts, art] };
    }
    case "risk_report": {
      const items = (ev.data.items ?? []) as RiskItem[];
      return { ...task, risk_report: items };
    }
    case "subtask_start": {
      const st = ev.data as SubTask & { parent_task_id?: string };
      const subtasks = [...(task.subtasks ?? [])];
      const idx = subtasks.findIndex((s) => s.subtask_id === st.subtask_id);
      const entry: SubTask = {
        subtask_id: st.subtask_id,
        name: st.name ?? "",
        status: st.status ?? "running",
        summary: st.summary ?? "",
        artifacts: st.artifacts ?? [],
        error: st.error ?? null,
      };
      if (idx >= 0) subtasks[idx] = entry;
      else subtasks.push(entry);
      return { ...task, subtasks };
    }
    case "subtask_result": {
      const st = ev.data as SubTask;
      const subtasks = (task.subtasks ?? []).map((s) =>
        s.subtask_id === st.subtask_id
          ? {
              ...s,
              status: st.status ?? "completed",
              summary: st.summary ?? s.summary,
              artifacts: st.artifacts ?? s.artifacts,
              error: st.error ?? null,
            }
          : s
      );
      return { ...task, subtasks };
    }
    case "subtask_failed": {
      const st = ev.data as { subtask_id: string; name: string; error: string };
      const subtasks = (task.subtasks ?? []).map((s) =>
        s.subtask_id === st.subtask_id
          ? { ...s, status: "failed", error: st.error ?? "subtask failed" }
          : s
      );
      return { ...task, subtasks };
    }
    case "final_answer":
      return { ...task, final_answer: ev.data.answer ?? task.final_answer };
    case "task_completed":
      return { ...task, status: "COMPLETED" };
    case "task_failed":
      return { ...task, status: "FAILED", error: ev.data.error ?? task.error };
    case "task_interrupted":
      return { ...task, status: "INTERRUPTED" };
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
  confirm: { open: false },
  selectedStep: null,

  setTasks: (tasks) => set({ tasks }),

  setCurrentTask: (id) => set({ currentTaskId: id, selectedStep: null }),

  upsertTask: (task) =>
    set((state) => {
      const detail = { ...state.detail, [task.id]: task };
      const exists = state.tasks.some((t) => t.id === task.id);
      const tasks = exists
        ? state.tasks.map((t) => (t.id === task.id ? task : t))
        : [task, ...state.tasks];
      return { detail, tasks };
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
        : [summary, ...state.tasks];

      let confirm = state.confirm;
      if (ev.type === "human_confirm_required") {
        confirm = {
          open: true,
          tool_call_id: ev.data.tool_call_id,
          tool_name: ev.data.tool_name,
          input: ev.data.input,
          task_id: id,
        };
      }

      // Track flow markers (circuit open / context compressed) per task so the
      // message stream can render them without losing cross-task isolation.
      let markers = state.markers;
      if (ev.type === "tool_circuit_open" || ev.type === "context_compressed") {
        const list = markers[id] ?? [];
        const marker: TraceMarker = { type: ev.type, ts: ev.ts, data: ev.data };
        markers = { ...markers, [id]: [...list, marker].slice(-200) };
      }

      return {
        detail: { ...state.detail, [id]: next },
        tasks,
        confirm,
        markers,
        events: [...state.events, ev].slice(-500),
      };
    }),

  openConfirm: (c) => set({ confirm: { open: true, ...c } }),
  closeConfirm: () => set({ confirm: { open: false } }),
  setSelectedStep: (index) => set({ selectedStep: index }),
}));
