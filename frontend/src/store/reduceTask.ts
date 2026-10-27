import { SSEvent, StepRecord, SubTask, Task, ToolCallRecord } from "../types";

function makeStep(index: number): StepRecord {
  return { index, thought: "", tool_calls: [], status: "running" };
}

function emptyTask(id: string): Task {
  return {
    id,
    title: "",
    user_input: "",
    workspace: "",
    model: "",
    status: "PENDING",
    mode: "build",
    steps: [],
    plan_version: 0,
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

/**
 * Apply a single SSE event to a task snapshot. `prev` is the snapshot the store
 * already holds for this task, or `undefined` when it has none yet (#189).
 *
 * A replayed `task_created` may only seed a missing snapshot; once a snapshot
 * exists it is authoritative, so the replay must not downgrade status or title.
 */
export function applyToTask(
  prev: Task | undefined,
  id: string,
  ev: SSEvent
): Task {
  if (ev.type === "task_created") {
    if (prev) return prev;
    return {
      ...emptyTask(id),
      id: ev.data.task_id ?? id,
      title: ev.data.title ?? "",
      status: ev.data.status ?? "PENDING",
    };
  }

  const task = prev ?? emptyTask(id);
  switch (ev.type) {
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
    case "subtask_start":
    case "subtask_result":
    case "subtask_failed": {
      const data = ev.data as {
        subtask_id?: string;
        name?: string;
        status?: string;
        summary?: string;
        artifacts?: string[];
        error?: string;
      };
      const subtaskId = data.subtask_id;
      if (!subtaskId) return task;

      const list = task.subtasks ?? [];
      const existing = list.find((s) => s.subtask_id === subtaskId);
      const base: SubTask = existing ?? {
        subtask_id: subtaskId,
        name: data.name ?? "",
        status: "pending",
        summary: "",
        artifacts: [],
      };
      const next: SubTask = { ...base };
      if (data.name !== undefined) next.name = data.name;

      if (ev.type === "subtask_start") {
        next.status = data.status ?? "running";
        next.summary = "";
        next.artifacts = [];
        next.error = null;
      } else if (ev.type === "subtask_result") {
        next.status = data.status ?? "completed";
        if (data.summary !== undefined) next.summary = data.summary;
        if (data.artifacts !== undefined) next.artifacts = data.artifacts;
      } else {
        next.status = "failed";
        if (data.error !== undefined) next.error = data.error;
      }

      const subtasks = existing
        ? list.map((s) => (s.subtask_id === subtaskId ? next : s))
        : [...list, next];
      return { ...task, subtasks };
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

/**
 * #189: a status refetch merges these fields only. The REST snapshot may lag the
 * live stream, so replacing the whole detail would wipe fresher steps /
 * conversation.
 */
export function mergeStatusSnapshot(prev: Task, snapshot: Task): Task {
  return {
    ...prev,
    status: snapshot.status,
    error: snapshot.error,
    updated_at: snapshot.updated_at,
  };
}

/**
 * #189: SSE can outrun the REST snapshot (still PENDING while steps already
 * stream). Callers refetch once per task when this returns true.
 */
export function needsStatusRefetch(task: Task, ev: SSEvent): boolean {
  return ev.type === "step_start" && task.status === "PENDING";
}
