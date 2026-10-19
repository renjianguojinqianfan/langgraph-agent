import { useEffect, useState } from "react";
import { HistoryPanel } from "../components/HistoryPanel";
import { StepDetail } from "../components/StepDetail";
import { TaskPanel } from "../components/TaskPanel";
import { getTask, listTasks } from "../api/client";
import { useSSE } from "../hooks/useSSE";
import { useTaskStore } from "../store/taskStore";
import { TaskViewTab } from "../types";
import { currentDir } from "../workspace";

export function TaskView() {
  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const detail = useTaskStore((s) =>
    currentTaskId ? s.detail[currentTaskId] : undefined
  );
  const applyEvent = useTaskStore((s) => s.applyEvent);
  const upsertTask = useTaskStore((s) => s.upsertTask);
  const setTasks = useTaskStore((s) => s.setTasks);
  const historyFilter = useTaskStore((s) => s.historyFilter);
  const [tab, setTab] = useState<TaskViewTab>("run");

  // History load: this page's directory by default, or every task (ADR 0010).
  useEffect(() => {
    const dir = historyFilter === "all" ? "all" : currentDir() ?? "current";
    listTasks(50, dir)
      .then((r) => {
        if (r.data?.tasks) setTasks(r.data.tasks);
      })
      .catch(() => {});
  }, [setTasks, historyFilter]);

  // Live SSE for the current task.
  useSSE(currentTaskId, applyEvent);

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
