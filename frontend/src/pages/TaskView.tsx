import { useCallback, useEffect, useRef, useState } from "react";
import { HistoryPanel } from "../components/HistoryPanel";
import { StepDetail } from "../components/StepDetail";
import { TaskPanel } from "../components/TaskPanel";
import { getTask, listTasks } from "../api/client";
import { useSSE } from "../hooks/useSSE";
import { useModelStore } from "../store/modelStore";
import { needsStatusRefetch } from "../store/reduceTask";
import { useTaskStore } from "../store/taskStore";
import { SSEvent, TaskViewTab } from "../types";
import { currentDir } from "../workspace";

export function TaskView() {
  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const detail = useTaskStore((s) =>
    currentTaskId ? s.detail[currentTaskId] : undefined
  );
  const applyEvent = useTaskStore((s) => s.applyEvent);
  const mergeTaskStatus = useTaskStore((s) => s.mergeTaskStatus);
  const upsertTask = useTaskStore((s) => s.upsertTask);
  const setTasks = useTaskStore((s) => s.setTasks);
  const historyFilter = useTaskStore((s) => s.historyFilter);
  const refreshModels = useModelStore((s) => s.refresh);
  const listRevision = useTaskStore((s) => s.listRevision);
  const [tab, setTab] = useState<TaskViewTab>("run");

  useEffect(() => {
    refreshModels();
  }, [refreshModels]);

  // History load: this page's directory by default, or every task (ADR 0010).
  // Membership is the server GET's answer; refetch on filter, create and select.
  useEffect(() => {
    let cancelled = false;
    const dir = historyFilter === "all" ? "all" : currentDir() ?? "current";
    listTasks(50, dir)
      .then((r) => {
        if (!cancelled && r.data?.tasks) setTasks(r.data.tasks);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [setTasks, historyFilter, listRevision, currentTaskId]);

  // #189: a step_start can arrive while the snapshot still says PENDING (no
  // event carries the RUNNING status). Refetch the snapshot once per task and
  // merge its status fields only — the live steps / conversation stay put.
  const statusRefetched = useRef<Set<string>>(new Set());
  const onEvent = useCallback(
    (ev: SSEvent) => {
      applyEvent(ev);
      const state = useTaskStore.getState();
      const id = ev.data?.task_id ?? state.currentTaskId ?? undefined;
      if (!id) return;
      const task = state.detail[id];
      if (!task || !needsStatusRefetch(task, ev)) return;
      if (statusRefetched.current.has(id)) return;
      statusRefetched.current.add(id);
      getTask(id)
        .then((r) => {
          if (r.data) mergeTaskStatus(r.data);
        })
        .catch(() => {});
    },
    [applyEvent, mergeTaskStatus]
  );

  // Live SSE for the current task.
  useSSE(currentTaskId, onEvent);

  // Fetch a fresh snapshot when the selected task changes.
  useEffect(() => {
    if (!currentTaskId) return;
    getTask(currentTaskId)
      .then((r) => {
        if (r.data) upsertTask(r.data);
      })
      .catch(() => {});
  }, [currentTaskId, upsertTask]);

  // A round just parked: pull the authoritative record (conversation, plan,
  // steps). SSE carries the live events but is replayed on every reconnect, so
  // the persisted snapshot — not event appending — is what the UI must trust.
  const status = detail?.status;
  useEffect(() => {
    if (!currentTaskId) return;
    if (status !== "INTERRUPTED" && status !== "FAILED") return;
    getTask(currentTaskId)
      .then((r) => {
        if (r.data) upsertTask(r.data);
      })
      .catch(() => {});
  }, [currentTaskId, status, detail?.final_answer, upsertTask]);

  // Switching tasks starts on the live run view by default.
  useEffect(() => {
    setTab("run");
  }, [currentTaskId]);

  const task = currentTaskId ? detail ?? null : null;

  return (
    <div className="h-full flex">
      <HistoryPanel />
      <TaskPanel task={task} tab={tab} onTabChange={setTab} />
      <StepDetail task={task} />
    </div>
  );
}
