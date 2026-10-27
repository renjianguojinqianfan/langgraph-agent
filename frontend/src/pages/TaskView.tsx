import { useCallback, useEffect, useRef, useState } from "react";
import { TaskRail } from "../components/rail/TaskRail";
import { TaskPanel } from "../components/TaskPanel";
import { DashTabs, type DashTab } from "../components/dash/DashTabs";
import { IconButton } from "../components/ui/Button";
import { getTask, listTasks } from "../api/client";
import { useSSE } from "../hooks/useSSE";
import { useModelStore } from "../store/modelStore";
import { needsStatusRefetch } from "../store/reduceTask";
import { useTaskStore } from "../store/taskStore";
import { SSEvent } from "../types";
import { cx } from "../lib/cx";
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
  const [dashTab, setDashTab] = useState<DashTab>("files");
  const [traceOpen, setTraceOpen] = useState(false);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);

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

  // Switching tasks closes the replay face.
  useEffect(() => {
    setTraceOpen(false);
  }, [currentTaskId]);

  const task = currentTaskId ? detail ?? null : null;

  return (
    <div
      className="relative flex h-full min-h-0 bg-surface-page text-primary"
      style={{
        paddingTop: "env(safe-area-inset-top)",
        paddingLeft: "env(safe-area-inset-left)",
        paddingRight: "env(safe-area-inset-right)",
        paddingBottom: "env(safe-area-inset-bottom)",
      }}
    >
      <aside
        id="task-rail"
        className={cx(
          "w-[264px] shrink-0 bg-surface-column border-r border-rule",
          "max-[860px]:fixed max-[860px]:inset-y-0 max-[860px]:left-0 max-[860px]:z-30 max-[860px]:w-[264px] max-[860px]:max-w-[80vw] max-[860px]:transition-transform max-[860px]:duration-[220ms] max-[860px]:ease-[cubic-bezier(.32,.72,0,1)]",
          leftOpen ? "max-[860px]:translate-x-0" : "max-[860px]:-translate-x-full"
        )}
      >
        <TaskRail />
      </aside>

      <main className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        <IconButton
          type="button"
          aria-label="打开任务列表"
          aria-controls="task-rail"
          aria-expanded={leftOpen}
          icon="panel"
          onClick={() => setLeftOpen((v) => !v)}
          className="min-[861px]:hidden absolute left-3 top-3 z-20"
        />
        <div
          className={cx(
            "mx-auto flex h-full w-full min-h-0 flex-col",
            traceOpen ? "" : "max-w-[760px]"
          )}
        >
          <TaskPanel
            task={task}
            rightOpen={rightOpen}
            onToggleRight={() => setRightOpen((v) => !v)}
            onOpenPlan={() => {
              setDashTab("plan");
              setRightOpen(true);
            }}
            traceOpen={traceOpen}
            onCloseTrace={() => setTraceOpen(false)}
          />
        </div>
      </main>

      <aside
        id="task-dash"
        className={cx(
          "w-[420px] shrink-0 bg-surface-column border-l border-rule",
          "max-[1180px]:fixed max-[1180px]:inset-y-0 max-[1180px]:right-0 max-[1180px]:z-30 max-[1180px]:w-[420px] max-[1180px]:max-w-[calc(100vw_-_2rem)] max-[1180px]:transition-transform max-[1180px]:duration-[220ms] max-[1180px]:ease-[cubic-bezier(.32,.72,0,1)]",
          rightOpen ? "max-[1180px]:translate-x-0" : "max-[1180px]:translate-x-full"
        )}
      >
        <DashTabs
          task={task}
          tab={dashTab}
          onTabChange={setDashTab}
          onOpenTrace={() => setTraceOpen(true)}
        />
      </aside>

      {(leftOpen || rightOpen) && (
        <button
          type="button"
          aria-label="关闭侧栏"
          className="fixed inset-0 z-20 bg-black/50 max-[1180px]:block hidden"
          onClick={() => {
            setLeftOpen(false);
            setRightOpen(false);
          }}
        />
      )}
    </div>
  );
}
