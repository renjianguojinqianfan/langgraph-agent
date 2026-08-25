import { useEffect, useState } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { HistoryPanel } from "../components/HistoryPanel";
import { KbPanel } from "../components/KbPanel";
import { StepDetail } from "../components/StepDetail";
import { TaskPanel } from "../components/TaskPanel";
import { getTask, listTasks } from "../api/client";
import { useSSE } from "../hooks/useSSE";
import { useTaskStore } from "../store/taskStore";
import { TaskViewTab } from "../types";

export function TaskView() {
  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const detail = useTaskStore((s) =>
    currentTaskId ? s.detail[currentTaskId] : undefined
  );
  const applyEvent = useTaskStore((s) => s.applyEvent);
  const upsertTask = useTaskStore((s) => s.upsertTask);
  const setTasks = useTaskStore((s) => s.setTasks);
  const [tab, setTab] = useState<TaskViewTab>("run");
  const [showKb, setShowKb] = useState(false);

  // Initial history load.
  useEffect(() => {
    listTasks()
      .then((r) => {
        if (r.data?.tasks) setTasks(r.data.tasks);
      })
      .catch(() => {});
  }, [setTasks]);

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

  // Switching tasks starts on the live run view by default.
  useEffect(() => {
    setTab("run");
  }, [currentTaskId]);

  const task = currentTaskId ? detail ?? null : null;

  return (
    <div className="h-full flex">
      <HistoryPanel />
      <TaskPanel
        task={task}
        tab={tab}
        onTabChange={setTab}
        onToggleKb={() => setShowKb((v) => !v)}
        kbOpen={showKb}
      />
      <StepDetail task={task} />
      {showKb && <KbPanel />}
      <ConfirmDialog />
    </div>
  );
}
