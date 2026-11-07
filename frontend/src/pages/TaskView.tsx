import { useCallback, useEffect, useRef, useState } from "react";
import { TaskRail } from "../components/rail/TaskRail";
import { TaskPanel } from "../components/TaskPanel";
import { DashTabs } from "../components/dash/DashTabs";
import { IconButton } from "../components/ui/Button";
import {
  closeFileTab,
  fileTabId,
  fileTabPath,
  openFileTab,
  tabAfterClosing,
  type DashTab,
} from "../lib/dashTabs";
import { getTask, getTaskSnapshot, listTasksSnapshot } from "../api/client";
import { useIsRightDrawer, useRailStatic } from "../lib/breakpoints";
import { drawerView, type DrawerSide } from "../lib/drawer";
import { useDrawerFocus } from "../hooks/useDrawerFocus";
import { useSSE } from "../hooks/useSSE";
import { useModelStore } from "../store/modelStore";
import { needsStatusRefetch } from "../store/reduceTask";
import { useTaskStore } from "../store/taskStore";
import { SSEvent } from "../types";
import { cx } from "../lib/cx";
import {
  detailLoadFailure,
  detailLoadState,
  listLoadOutcome,
} from "../lib/failureStates";
import { isParked } from "../lib/taskState";
import { shouldRefetchParked } from "../lib/requestPlan";

export function TaskView() {
  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const detail = useTaskStore((s) =>
    currentTaskId ? s.detail[currentTaskId] : undefined
  );
  const applyEvent = useTaskStore((s) => s.applyEvent);
  const mergeTaskStatus = useTaskStore((s) => s.mergeTaskStatus);
  const upsertTask = useTaskStore((s) => s.upsertTask);
  const setTasks = useTaskStore((s) => s.setTasks);
  const setListError = useTaskStore((s) => s.setListError);
  const setListLoading = useTaskStore((s) => s.setListLoading);
  const setDetailError = useTaskStore((s) => s.setDetailError);
  const setAuthExpired = useTaskStore((s) => s.setAuthExpired);
  const detailRevision = useTaskStore((s) => s.detailRevision);
  const historyFilter = useTaskStore((s) => s.historyFilter);
  const workspaceDir = useTaskStore((s) => s.workspaceDir);
  const refreshModels = useModelStore((s) => s.refresh);
  const listRevision = useTaskStore((s) => s.listRevision);
  // File previews are tabs in the third column (#266), one per opened path.
  // Tab and file list move together so a close never reads a stale list.
  const [dash, setDash] = useState<{ tab: DashTab; files: string[] }>({
    tab: "files",
    files: [],
  });
  const [traceOpen, setTraceOpen] = useState(false);
  // D-25: one breakpoint source for both columns (lib/breakpoints.ts); rail
  // static is the primary fact and a drawer is its negation.
  const railStatic = useRailStatic();
  const isLeftDrawer = !railStatic;
  const isRightDrawer = useIsRightDrawer();
  // D-26: a single open entry keeps the two drawers mutually exclusive.
  const [open, setOpen] = useState<DrawerSide | null>(null);
  const railRef = useRef<HTMLElement>(null);
  const dashRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const skipNavRef = useRef<HTMLElement>(null);
  // D-30: last status the parked effect observed. null = none yet, i.e. the
  // status about to arrive comes from the snapshot and must not refetch.
  const prevStatusRef = useRef<string | null>(null);
  const view = drawerView({
    leftNarrow: isLeftDrawer,
    rightNarrow: isRightDrawer,
    open,
    traceOpen,
  });
  const railInert = isLeftDrawer && view.justify !== "left";
  const dashInert = isRightDrawer && view.justify !== "right";

  // D-27: the hook is the only writer of `inert`/`aria-hidden` (background
  // targets while justified, off-screen drawer edges when narrow-but-closed)
  // and owns focus entry/trap/Esc/restore. `active` follows D-26's `justify`,
  // so a drawer that stopped being narrow or hid behind Trace never traps.
  const containerRef = view.justify === "right" ? dashRef : railRef;
  const inertTargets =
    view.justify === "left"
      ? [mainRef, dashRef, skipNavRef]
      : view.justify === "right"
        ? [mainRef, railRef, skipNavRef]
        : [];
  useDrawerFocus({
    active: view.justify !== null,
    containerRef,
    inertTargets,
    backgroundInert: view.backgroundInert,
    edges: [
      { ref: railRef, inert: railInert },
      { ref: dashRef, inert: dashInert },
    ],
    onClose: () => setOpen(null),
  });

  // D-26: widening past the breakpoint (or opening Trace) closes the drawer, so
  // no scrim can outlive the state that justified it.
  useEffect(() => {
    if (view.closeOnWiden || view.closeForTrace) setOpen(null);
  }, [view.closeOnWiden, view.closeForTrace]);

  useEffect(() => {
    refreshModels();
  }, [refreshModels]);

  // History load: this page's workspace by default, or every task (ADR 0010).
  // Membership is the server GET's answer; refetch on filter, create, select
  // and workspace switch (#242).
  useEffect(() => {
    let cancelled = false;
    const dir = historyFilter === "all" ? "all" : workspaceDir || "current";
    // #256: the empty copy waits for the answer — a pending list must not
    // read as "no tasks", and a failed one must not either. `listTasksSnapshot`
    // never rejects (a transport failure comes back as status 0), so there is
    // no catch to write: every answer is decided in `then`.
    setListLoading(true);
    listTasksSnapshot(50, dir)
      .then((r) => {
        if (cancelled) return;
        const outcome = listLoadOutcome(r.status, r.data?.tasks ?? null);
        if (outcome.ok) {
          setTasks(outcome.tasks);
          setListError("");
          setAuthExpired("list", false);
        } else {
          setListError(outcome.error);
          setAuthExpired("list", outcome.authExpired);
        }
      })
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    setTasks,
    setListError,
    setListLoading,
    setAuthExpired,
    historyFilter,
    listRevision,
    currentTaskId,
    workspaceDir,
  ]);

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

  // Fetch a fresh snapshot when the selected task changes. #256: the status
  // travels with the answer — a 404 (or a dead backend) is a failed load, not
  // a missing task that the center column may render as "新任务". The reader
  // never rejects, so there is no catch: every answer is decided in `then`.
  useEffect(() => {
    if (!currentTaskId) return;
    let cancelled = false;
    getTaskSnapshot(currentTaskId)
      .then((r) => {
        if (cancelled) return;
        // `hasData` splits a real snapshot from a 2xx whose envelope carries
        // none (a wrong VITE_API_BASE, an SPA-fallback HTML page): the latter
        // is a failed load too — an empty copy would delete the detailError
        // entry and park the column on "加载中…" forever (复审回归).
        const state = detailLoadState(r.status, r.data !== null);
        if (state === "ready" && r.data) {
          setDetailError(currentTaskId, "");
          setAuthExpired("detail", false);
          upsertTask(r.data);
          // D-30: the snapshot already carries the authoritative status — the
          // parked effect must not spend a second getTask on it.
          prevStatusRef.current = r.data.status;
        } else {
          setDetailError(currentTaskId, detailLoadFailure(state, r.status));
          setAuthExpired("detail", state === "auth");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentTaskId, upsertTask, setDetailError, setAuthExpired, detailRevision]);

  // A round just parked: pull the authoritative record (conversation, plan,
  // steps). SSE carries the live events but is replayed on every reconnect, so
  // the persisted snapshot — not event appending — is what the UI must trust.
  // D-30 / #258 AC4: only a transition into parked refetches. The status a
  // fresh snapshot just delivered is already authoritative, so selecting an
  // already-parked task is one GET, not two.
  const status = detail?.status;
  useEffect(() => {
    if (!currentTaskId || !status) return;
    const prevStatus = prevStatusRef.current;
    prevStatusRef.current = status;
    if (!shouldRefetchParked(prevStatus, status)) return;
    getTask(currentTaskId)
      .then((r) => {
        if (r.data) upsertTask(r.data);
      })
      .catch(() => {});
  }, [currentTaskId, status, detail?.final_answer, upsertTask]);

  // Switching tasks closes the replay face and every file-preview tab; the
  // plan/events choice is not a file tab and survives the switch.
  useEffect(() => {
    // A switch starts from "nothing observed": the next status comes from the
    // new task's snapshot, not from a live→parked transition.
    prevStatusRef.current = null;
    setTraceOpen(false);
    setDash((current) => ({
      files: [],
      tab: fileTabPath(current.tab) !== null ? "files" : current.tab,
    }));
  }, [currentTaskId]);

  const openFile = useCallback((path: string) => {
    setDash((current) => ({
      files: openFileTab(current.files, path),
      tab: fileTabId(path),
    }));
    // On narrow screens the file list lives in the drawer; keep it open when
    // its row turns into a preview tab.
    setOpen("right");
  }, []);

  const closeFile = useCallback((path: string) => {
    setDash((current) => ({
      files: closeFileTab(current.files, path),
      tab: tabAfterClosing(current.files, path, current.tab),
    }));
  }, []);

  const changeTab = useCallback((tab: DashTab) => {
    setDash((current) => ({ ...current, tab }));
  }, []);

  const task = currentTaskId ? detail ?? null : null;
  // #256: a selected task whose snapshot failed stays a failure — never the
  // "新任务" face (sending there would silently create a second task). While
  // the snapshot is in flight the center column says so instead of pretending
  // to be a new task.
  const loadError = useTaskStore((s) =>
    currentTaskId ? (s.detailError[currentTaskId] ?? "") : ""
  );
  const loading = !!currentTaskId && !task && !loadError;

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
      {task && !traceOpen && (
        <nav ref={skipNavRef} aria-label="跳到对话记录">
          <a
            href="#conversation"
            className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-control focus:bg-surface-sheet focus:px-3 focus:py-1.5 focus:text-meta focus:text-primary"
          >
            跳到对话记录
          </a>
        </nav>
      )}

      <aside
        id="task-rail"
        ref={railRef}
        aria-label={view.justify === "left" ? "任务列表" : "任务历史"}
        role={view.justify === "left" ? "dialog" : undefined}
        aria-modal={view.justify === "left" ? true : undefined}
        className={cx(
          "w-[264px] shrink-0 bg-surface-column border-r border-rule",
          "max-[860px]:fixed max-[860px]:inset-y-0 max-[860px]:left-0 max-[860px]:z-30 max-[860px]:w-[264px] max-[860px]:max-w-[80vw] max-[860px]:transition-transform max-[860px]:duration-[220ms] max-[860px]:ease-[cubic-bezier(.32,.72,0,1)]",
          open === "left" ? "max-[860px]:translate-x-0" : "max-[860px]:-translate-x-full"
        )}
      >
        {view.justify === "left" && (
          <div className="flex shrink-0 justify-end border-b border-rule px-2 py-2">
            <IconButton
              type="button"
              aria-label="关闭任务列表"
              icon="close"
              onClick={() => setOpen(null)}
            />
          </div>
        )}
        <TaskRail />
      </aside>

      <main ref={mainRef} className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {/* D-25: the toggle renders iff the rail is actually a drawer — the old
            861px 断点变体（hidden）第三来源已删除；860 恰好是静态 rail。
            Trace 打开时也隐藏，避免「点了没反应」的错觉。 */}
        {isLeftDrawer && !traceOpen && (
          <IconButton
            type="button"
            aria-label="打开任务列表"
            aria-controls="task-rail"
            aria-expanded={open === "left"}
            icon="panel"
            onClick={() => setOpen((v) => (v === "left" ? null : "left"))}
            className="absolute left-3 top-3 z-20"
          />
        )}
        <div
          className={cx(
            "mx-auto flex h-full w-full min-h-0 flex-col",
            traceOpen ? "" : "max-w-[760px]"
          )}
        >
          <TaskPanel
            task={task}
            loadError={loadError}
            loading={loading}
            rightOpen={open === "right"}
            onToggleRight={() => setOpen((v) => (v === "right" ? null : "right"))}
            rightDrawer={isRightDrawer && !traceOpen}
            onOpenPlan={() => {
              changeTab("plan");
              setOpen("right");
            }}
            traceOpen={traceOpen}
            onCloseTrace={() => setTraceOpen(false)}
          />
        </div>
      </main>

      <aside
        id="task-dash"
        ref={dashRef}
        aria-label={view.justify === "right" ? "工作区与文件" : "工作区"}
        role={view.justify === "right" ? "dialog" : undefined}
        aria-modal={view.justify === "right" ? true : undefined}
        className={cx(
          "w-[420px] shrink-0 bg-surface-column border-l border-rule",
          "max-[1180px]:fixed max-[1180px]:inset-y-0 max-[1180px]:right-0 max-[1180px]:z-30 max-[1180px]:w-[420px] max-[1180px]:max-w-[calc(100vw_-_2rem)] max-[1180px]:transition-transform max-[1180px]:duration-[220ms] max-[1180px]:ease-[cubic-bezier(.32,.72,0,1)]",
          open === "right" ? "max-[1180px]:translate-x-0" : "max-[1180px]:translate-x-full"
        )}
      >
        {view.justify === "right" && (
          <div className="flex shrink-0 justify-end border-b border-rule px-2 py-2">
            <IconButton
              type="button"
              aria-label="关闭工作区"
              icon="close"
              onClick={() => setOpen(null)}
            />
          </div>
        )}
        <DashTabs
          task={task}
          tab={dash.tab}
          fileTabs={dash.files}
          visible={!dashInert}
          onTabChange={changeTab}
          onOpenFile={openFile}
          onCloseFile={closeFile}
          onOpenTrace={() => setTraceOpen(true)}
        />
      </aside>

      {/* D-26: the scrim renders iff a drawer is justified — never from `open`
          alone, so widening can't leave an orphan overlay. Decorative for the
          keyboard (Phase B makes Esc the path), hence aria-hidden/tabIndex=-1. */}
      {view.overlayVisible && (
        <button
          type="button"
          aria-label="关闭侧栏"
          aria-hidden="true"
          tabIndex={-1}
          className="fixed inset-0 z-20 bg-black/50"
          onClick={() => setOpen(null)}
        />
      )}
    </div>
  );
}
