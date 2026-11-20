import { useCallback, useEffect, useState } from "react";
import { listFiles } from "../api/client";
import { createFilePollLoop } from "../lib/filePolling";
import { filesLoadFailure } from "../lib/failureStates";
import { shouldPollFiles } from "../lib/taskState";
import type { Task, WorkspaceFile } from "../types";

/** Refresh cadence while a running task keeps writing files. */
const POLL_MS = 2000;

export interface WorkspaceFilesState {
  files: WorkspaceFile[];
  truncated: boolean;
  note: string | null;
  loading: boolean;
  retry: () => void;
}

/**
 * #341 polling lifted out of FilePane so the Files tab and the #346 navigator
 * share one request loop and one list. `visible` is the host's "this surface
 * actually needs data" signal: a closed right drawer (or a preview tab whose
 * navigator is closed) must not fetch (#230).
 */
export function useWorkspaceFiles(
  task: Task | null,
  visible: boolean
): WorkspaceFilesState {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  /** Bumped by the retry button; the load effect keys on it (#258 AC5). */
  const [reloadToken, setReloadToken] = useState(0);

  const taskId = task?.id ?? null;
  const status = task?.status ?? null;

  // A different task starts from a clean list; a status change must not clear
  // the list the user is reading.
  useEffect(() => {
    setFiles([]);
    setTruncated(false);
    setNote(null);
    setLoading(true);
  }, [taskId]);

  useEffect(() => {
    if (!taskId || status === null || !visible) return;

    setLoading(true);
    // #335: poll only after each request settles, so a slow scan cannot stack
    // requests. `stop()` drops the browser-side response of an in-flight scan
    // (task switch / hidden / retry) but cannot stop the server-side scan.
    const controller = new AbortController();
    const loop = createFilePollLoop({
      intervalMs: POLL_MS,
      run: () => listFiles(taskId, controller.signal),
      shouldContinue: () => shouldPollFiles(status, true),
      apply: (r) => {
        setLoading(false);
        if (!r.data) {
          setNote(filesLoadFailure(r.status, r.detail));
          return;
        }
        setNote(null);
        setFiles(r.data.files ?? []);
        setTruncated(!!r.data.truncated);
      },
      // `reqWithStatus` never rejects (a transport failure already comes back
      // as status 0) — kept as defence in depth, not a silent dead path.
      onError: () => {
        setLoading(false);
        setNote(filesLoadFailure(0));
      },
    });
    loop.start();
    return () => {
      // Invalidate first, then cancel the browser request: an abort rejection
      // must not resurrect an error note for a pane that already moved on.
      loop.stop();
      controller.abort();
    };
  }, [taskId, status, visible, reloadToken]);

  const retry = useCallback(() => setReloadToken((n) => n + 1), []);

  return { files, truncated, note, loading, retry };
}
