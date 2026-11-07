import { useEffect, useMemo, useRef, useState } from "react";
import { apiErrorText, getPlanSnapshot, putPlan } from "../../api/client";
import { Markdown } from "../../lib/markdown";
import { planLoadFailure, planLoadState } from "../../lib/failureStates";
import { LONG_TEXT } from "../../lib/longContent";
import { nextPlanVersion, shouldReloadOnPlanVersion } from "../../lib/planVersion";
import { isParked } from "../../lib/taskState";
import { useTaskStore } from "../../store/taskStore";
import type { Task } from "../../types";
import { Button } from "../ui/Button";
import { ReauthAction } from "../ReauthAction";
import { Status } from "../ui/Status";
import { parsePlanToc, type TocItem } from "./planToc";

function PlanToc({ items }: { items: TocItem[] }) {
  if (items.length === 0) return null;
  return (
    <nav
      aria-label="计划目录"
      className="rounded-well bg-surface-well px-3 py-2"
    >
      <ul className="flex flex-col gap-0.5">
        {items.map((item) => (
          <li key={item.line} className={item.level === 3 ? "pl-3" : undefined}>
            <a
              href={`#${item.slug}`}
              onClick={(e) => {
                e.preventDefault();
                document
                  .getElementById(item.slug)
                  ?.scrollIntoView({ block: "start" });
              }}
              className={`${LONG_TEXT} flex min-h-6 items-center text-meta text-secondary hover:text-primary`}
            >
              {item.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function PlanPane({ task }: { task: Task }) {
  const [text, setText] = useState("");
  const [version, setVersion] = useState(0);
  /** Text as loaded into the editor; save compares the fresh file against it. */
  const [loaded, setLoaded] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  /** #256: why the plan could not be read; non-empty blocks every save. */
  const [loadError, setLoadError] = useState("");
  /** Bumped by the retry button; the load effect keys on it. */
  const [reloadToken, setReloadToken] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{
    text: string;
    version: number;
  } | null>(null);
  // #256: a saved plan version must reach the store, or PlanAnchor keeps
  // showing the stale one and the next write starts from the wrong version.
  const setPlanVersion = useTaskStore((s) => s.setPlanVersion);
  // #256 (复审): a 401 on the plan read needs the re-login entry — the retry
  // button cannot fix a dead token. Per-source flag, like the other faces.
  const planAuthExpired = useTaskStore((s) => s.authExpired.plan);
  const setAuthExpired = useTaskStore((s) => s.setAuthExpired);

  const editable = isParked(task.status);
  // D-30: the load effect no longer keys on `task.plan_version` (a save synced
  // it back through the store and re-triggered a second GET). Read the store's
  // latest version through a ref instead; `applyVersion` keeps it in sync.
  const latestVersionRef = useRef(task.plan_version);
  latestVersionRef.current = task.plan_version;
  // #258 终稿: the version this pane last synced (load / save / retry). A
  // store-side version ahead of it means an external change needs one refetch.
  const lastSyncedVersionRef = useRef(0);
  // A load currently in flight — the version effect must not queue a second GET.
  const loadInFlightRef = useRef(false);

  /**
   * Mirror the server's version onto the task (#256, PlanAnchor). Takes the
   * max with the store's: plan versions only ever grow, and the one non-
   * monotonic read is the empty plan file (version 0) — which must not walk
   * a real version back (初审 LOW：初载只 setVersion 会让锚点陈旧).
   */
  const applyVersion = (next: number) => {
    const merged = nextPlanVersion(latestVersionRef.current, next);
    // The store write renders a tick later; keep the ref authoritative for
    // any further read in this cycle.
    latestVersionRef.current = merged;
    lastSyncedVersionRef.current = merged;
    setVersion(merged);
    setPlanVersion(task.id, merged);
  };

  useEffect(() => {
    lastSyncedVersionRef.current = 0;
    loadInFlightRef.current = false;
    setEditing(false);
    setNote(null);
    setConflict(null);
    setText("");
    setVersion(0);
    setLoaded("");
    setLoadError("");
    setLoading(true);
  }, [task.id]);

  useEffect(() => {
    if (editing) return;
    let cancelled = false;
    loadInFlightRef.current = true;
    setLoading(true);
    // `getPlanSnapshot` never rejects (transport failure → status 0), so every
    // answer is decided in `then` — no catch to write here.
    getPlanSnapshot(task.id)
      .then((r) => {
        if (cancelled) return;
        // #256: a failed read is an error face, never an empty plan — the
        // empty face is what let an empty PUT overwrite the real file.
        if (planLoadState(r.status) !== "ready" || !r.data) {
          setLoadError(planLoadFailure(r.status, r.detail));
          setAuthExpired("plan", r.status === 401);
          return;
        }
        setLoadError("");
        setAuthExpired("plan", false);
        setText(r.data.text);
        // #256 (复审 LOW): the first read syncs the anchor too — waiting for
        // a save / 载入新版 left PlanAnchor stale whenever the server was
        // ahead of the store. `applyVersion` takes the max, so the empty-text
        // read (version 0) can never walk a real version back.
        applyVersion(r.data.version);
        setLoaded(r.data.text);
      })
      .finally(() => {
        if (!cancelled) {
          loadInFlightRef.current = false;
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
      loadInFlightRef.current = false;
    };
  }, [task.id, task.status, editing, reloadToken]);

  // #258 终稿（MEDIUM）: after D-30 removed `task.plan_version` from the load
  // effect, an external version bump (detailRevision snapshot / another tab's
  // save) could leave stale text on screen. Reload once when the store moved
  // ahead of what we synced; `loadInFlightRef` coalesces with a simultaneous
  // status-triggered load so the same render never fires two GETs.
  const storedVersion = task.plan_version;
  useEffect(() => {
    if (editing || loadInFlightRef.current) return;
    if (!shouldReloadOnPlanVersion(storedVersion, lastSyncedVersionRef.current)) {
      return;
    }
    setReloadToken((n) => n + 1);
  }, [storedVersion, editing]);

  const startEdit = () => {
    setLoaded(text);
    setEditing(true);
    setNote(null);
    setConflict(null);
  };

  const cancel = () => {
    setText(loaded);
    setEditing(false);
    setNote(null);
    setConflict(null);
  };

  const doPut = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await putPlan(task.id, text);
      if (r.data?.ok) {
        applyVersion(r.data.version);
        setLoaded(text);
        setEditing(false);
        setConflict(null);
        setNote("已保存；下一条消息生效");
      } else {
        setNote(apiErrorText(r));
      }
    } catch {
      setNote("保存失败");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      // GET has no hash, so "has the file changed since I opened the editor?"
      // is answered by re-reading the text and comparing it locally. A failed
      // re-read blocks the save (#256): without a fresh read there is no proof
      // the file still matches, and an empty editor must never reach the disk.
      // The reader never rejects, so this is straight-line code.
      const fresh = await getPlanSnapshot(task.id);
      if (planLoadState(fresh.status) !== "ready" || !fresh.data) {
        setNote(planLoadFailure(fresh.status, fresh.detail));
        return;
      }
      const freshText = fresh.data.text;
      if (freshText !== loaded) {
        setConflict({
          text: freshText,
          version: fresh.data.version,
        });
        setNote("计划文件在编辑期间被外部修改；请选择载入新版或覆盖保存。");
        return;
      }
      const r = await putPlan(task.id, text);
      if (r.data?.ok) {
        applyVersion(r.data.version);
        setLoaded(text);
        setEditing(false);
        setConflict(null);
        setNote("已保存；下一条消息生效");
      } else {
        setNote(apiErrorText(r));
      }
    } catch {
      setNote("保存失败");
    } finally {
      setBusy(false);
    }
  };

  const loadFresh = () => {
    if (!conflict) return;
    setText(conflict.text);
    setLoaded(conflict.text);
    // #256 (评审 AC4): the fresh version must reach the store too — a
    // local-only setVersion left PlanAnchor on the stale one.
    applyVersion(conflict.version);
    setConflict(null);
    setNote("已载入新版；继续编辑后再保存。");
  };

  // D-30: cache by text so `headingIds` is referentially stable across
  // unrelated re-renders — otherwise the memoized Markdown never hits.
  const toc = useMemo(() => parsePlanToc(text), [text]);
  const headingIds = useMemo(
    () => new Map(toc.map((item) => [item.line, item.slug] as const)),
    [toc]
  );
  // #256: with the plan unread, the editor holds nothing true — no edit, no
  // save; the only way forward is reading it again.
  const saveBlocked = !!loadError;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-w-0 items-center gap-2">
        <span className="shrink-0 text-meta text-secondary">计划 v{version}</span>
        <Status status={task.status} live={false} />
        {!editing && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto shrink-0"
            onClick={startEdit}
            disabled={!editable || saveBlocked}
            aria-describedby={
              !editable ? "plan-edit-reason" : saveBlocked ? "plan-load-error" : undefined
            }
          >
            {text.trim() ? "编辑" : "编写计划"}
          </Button>
        )}
        {editing && (
          <>
            <Button
              variant="outline"
              size="sm"
              className="ml-auto shrink-0"
              onClick={save}
              disabled={busy}
            >
              {busy ? "保存中…" : "保存计划"}
            </Button>
            <Button size="sm" onClick={cancel} disabled={busy}>
              取消
            </Button>
          </>
        )}
      </div>

      {!editable && (
        <p id="plan-edit-reason" className="text-meta text-tertiary">
          {task.status === "PENDING"
            ? "任务启动中；停下后可编辑计划。"
            : "任务运行中；停下后可编辑计划，改动在下一条消息生效。"}
        </p>
      )}

      {loadError && (
        <div
          id="plan-load-error"
          role="alert"
          className="flex flex-col items-start gap-2 rounded-well border border-state-danger/30 bg-state-danger/5 px-3 py-2"
        >
          <span className="text-meta text-state-danger">{loadError}</span>
          {/* #256 (复审): a 401 needs the re-login entry — retrying a dead
              token fails forever. */}
          {planAuthExpired ? (
            <ReauthAction />
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setReloadToken((n) => n + 1)}
              disabled={loading}
            >
              {loading ? "重试中…" : "重试"}
            </Button>
          )}
        </div>
      )}

      {note && (
        <p role="status" className="text-meta text-secondary">
          {note}
        </p>
      )}

      {conflict && (
        <div className="flex flex-wrap items-center gap-2 rounded-well border border-state-warn/40 bg-state-warn/5 px-3 py-2">
          <span className="text-meta text-state-warn">
            文件已是第 {conflict.version} 版。
          </span>
          <Button variant="outline" size="sm" onClick={loadFresh}>
            载入新版
          </Button>
          <Button variant="danger" size="sm" onClick={doPut} disabled={busy}>
            覆盖保存
          </Button>
        </div>
      )}

      {editing ? (
        <textarea
          aria-label="计划文本"
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          className="min-h-[280px] w-full resize-y rounded-well border border-rule-strong bg-surface-well px-3 py-2 font-mono text-code text-primary"
        />
      ) : loadError ? null : text.trim() ? (
        <>
          <PlanToc items={toc} />
          <Markdown headingIds={headingIds}>{text}</Markdown>
        </>
      ) : (
        <p className="text-body text-tertiary">
          {loading
            ? "加载中…"
            : task.mode === "plan" && task.status === "RUNNING"
              ? "计划生成中…"
              : "还没有计划。停下后可以手动写一份，或切到计划模式问一次。"}
        </p>
      )}
    </div>
  );
}
