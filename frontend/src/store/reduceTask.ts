import { SSEvent, StepRecord, SubTask, Task, ToolCallRecord } from "../types";

function makeStep(index: number, round?: number): StepRecord {
  return {
    index,
    ...(round !== undefined ? { round } : {}),
    thought: "",
    tool_calls: [],
    status: "running",
  };
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

function ensureStep(task: Task, index: number, round?: number): Task {
  if (task.steps.some((s) => s.index === index)) return task;
  const steps = [...task.steps, makeStep(index, round)].sort((a, b) => a.index - b.index);
  return { ...task, steps };
}

function findCallOwner(steps: StepRecord[], id: string): StepRecord | undefined {
  return steps.find((s) => s.tool_calls.some((tc) => tc.id === id));
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
      const round = typeof ev.data.round === "number" ? ev.data.round : undefined;
      const t = ensureStep(task, ev.data.index, round);
      const steps = t.steps.map((s) => {
        if (s.index !== ev.data.index) return s;
        // #300/#265 replay guard: the task snapshot is authoritative, but an
        // SSE reconnect replays the bus buffer (step_start included). A
        // replayed start must not downgrade a step the snapshot already
        // settled (done/failed) back to "running", nor overwrite its thought
        // (the backend copies the answer into the final step's thought).
        const terminal = s.status === "done" || s.status === "failed";
        return {
          ...s,
          ...(round !== undefined ? { round } : {}),
          thought: terminal ? s.thought : (ev.data.thought ?? s.thought),
          status: terminal ? s.status : "running",
        };
      });
      return { ...t, steps };
    }
    case "tool_call": {
      const rec = ev.data as ToolCallRecord;
      // Replay guard (P5-1 manual test): a reconnect replays the bus buffer
      // onto a REST snapshot that already carries these calls. Attribute the
      // call to the step that already owns its id — appending it to the last
      // step doubled the counts there and flipped that settled step back to
      // "running" (the round header then read 执行中).
      const owner = findCallOwner(task.steps, rec.id);
      if (owner) {
        const steps = task.steps.map((s) => {
          if (s !== owner) return s;
          const tool_calls = s.tool_calls.map((tc) =>
            // The snapshot may already hold the settled result; a replayed
            // dispatch frame must not walk output/status back to pending.
            tc.id === rec.id ? { ...rec, ...tc } : tc
          );
          return { ...s, tool_calls };
        });
        return { ...task, steps };
      }
      // A call no step has seen yet is live: it belongs to the step currently
      // streaming. (Replay of an older call is caught by the owner branch.)
      const steps = task.steps.length ? [...task.steps] : [makeStep(1)];
      const lastIdx = steps.length - 1;
      const last = steps[lastIdx];
      steps[lastIdx] = {
        ...last,
        tool_calls: [...last.tool_calls, rec],
        status: "running",
      };
      return { ...task, steps };
    }
    case "tool_result": {
      const rec = ev.data as ToolCallRecord;
      const owner = findCallOwner(task.steps, rec.id);
      if (!owner) return task;
      const steps = task.steps.map((s) => {
        if (s !== owner) return s;
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
    case "final_answer": {
      // The conversation itself is refreshed from REST when the round parks
      // (see TaskView): appending here would duplicate every turn whenever the
      // SSE buffer is replayed on reconnect.
      const answer = ev.data.answer ?? task.final_answer;
      // D-38/D-40: the event carries the redacted reasoning; attach it to the
      // step that just produced the answer so the collapsed "思考" block is
      // available without waiting for the parked snapshot (the snapshot stays
      // authoritative and replaces this on reload). Empty / absent reasoning
      // adds nothing — non-thinking models must not get an empty block.
      const reasoning =
        typeof ev.data.reasoning === "string" ? ev.data.reasoning : "";
      const lastStep = task.steps[task.steps.length - 1];
      const lastReasoning =
        lastStep && typeof lastStep.reasoning === "string" ? lastStep.reasoning : "";
      // #300 MEDIUM-1: SSE reconnect replays the bus buffer, so an old round's
      // final_answer can arrive after the next round already opened a step.
      // Only a *new* answer can own the current last step; a replay (answer
      // already equal to task.final_answer), a missing step, or an existing
      // different reasoning on that step are all stale — never attach. (When
      // two rounds produce identical answer text the late attach is dropped;
      // the parked snapshot remains authoritative.)
      const stale =
        !reasoning ||
        !lastStep ||
        answer === task.final_answer ||
        lastReasoning !== "";
      if (stale) {
        return { ...task, final_answer: answer };
      }
      const last = task.steps.length - 1;
      const steps = task.steps.map((step, index) =>
        index === last ? { ...step, reasoning } : step
      );
      return { ...task, steps, final_answer: answer };
    }
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
 * conversation. Direction: snapshot → prev, status fields only.
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
 * #256: apply a refetched snapshot without letting a late one wipe the live
 * stream. A snapshot requested before the stream caught up carries fewer
 * steps than the detail already holds — that snapshot is provably older, so
 * its steps / conversation / subtasks / final answer (and the status that
 * came with them, #189) lose to the live detail. A snapshot with at least as
 * many steps is fresh and wins outright (same tie-break as before: REST is
 * authoritative).
 *
 * The plan version is the one field both branches take with `max`: it only
 * ever grows on the server, and an in-flight GET requested across a save can
 * come back with the pre-save version at an *equal* step count — replacing
 * wholesale there walked a saved v5 back to v1 (复审 AC4 相等步数分支).
 *
 * vs `mergeStatusSnapshot`: that one keeps prev wholesale and takes three
 * status fields (the #189 status-only refetch); this one takes the snapshot
 * wholesale unless it is provably stale. The step count is the staleness
 * proxy — no event carries a monotonic sequence, and `updated_at` is written
 * by REST only.
 */
export function mergeSnapshotUnlessStale(prev: Task, snapshot: Task): Task {
  const plan_version = Math.max(prev.plan_version, snapshot.plan_version);
  const live = prev.steps.length > snapshot.steps.length;
  if (!live) return { ...snapshot, plan_version };
  return {
    ...snapshot,
    steps: prev.steps,
    conversation: prev.conversation,
    subtasks: prev.subtasks,
    final_answer: prev.final_answer,
    // A saved plan version lives on the task; a stale snapshot would walk it
    // back and the next save would start from the wrong version (AC4).
    plan_version,
    status: prev.status,
    error: prev.error,
  };
}

/**
 * #189: SSE can outrun the REST snapshot (still PENDING while steps already
 * stream). Callers refetch once per task when this returns true.
 */
export function needsStatusRefetch(task: Task, ev: SSEvent): boolean {
  return ev.type === "step_start" && task.status === "PENDING";
}
