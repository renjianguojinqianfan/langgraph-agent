import { useCallback, useState } from "react";
import { apiErrorText, createTask, sendMessage } from "../api/client";
import { clearedDraft, composerView } from "../lib/composerState";
import { BACKEND_UNREACHABLE } from "../lib/copy";
import { useTaskStore } from "../store/taskStore";
import type { Task, TaskMode } from "../types";

/** D-32 / #232: owns the draft text, the in-flight flag and the send-failure
 * line, plus the two send-side APIs (createTask / sendMessage). The guard bits
 * come from the same `composerView` the component renders, so the two never
 * disagree about "can this be sent". */
export interface UseComposerSendOptions {
  task: Task | undefined;
  currentTaskId: string | null;
  detailError: string;
  detailAuthExpired: boolean;
  draftMode: TaskMode;
  draftModel: string;
  defaultModelId: string;
}

export function useComposerSend({
  task,
  currentTaskId,
  detailError,
  detailAuthExpired,
  draftMode,
  draftModel,
  defaultModelId,
}: UseComposerSendOptions) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const setCurrent = useTaskStore((s) => s.setCurrentTask);
  const upsert = useTaskStore((s) => s.upsertTask);
  const workspaceDir = useTaskStore((s) => s.workspaceDir);

  const view = composerView({
    task,
    currentTaskId,
    detailError,
    detailAuthExpired,
    text,
    busy,
    sendError,
  });

  /** Switching tasks must not carry a previous conversation's error over. */
  const reset = useCallback(() => setSendError(null), []);

  const onSubmit = useCallback(async () => {
    const input = text.trim();
    if (!input || busy || view.inputDisabled) return;
    setBusy(true);
    setSendError(null);
    try {
      if (!task) {
        const res = await createTask({
          input,
          mode: draftMode,
          workspace: workspaceDir || undefined,
          model: draftModel || defaultModelId || undefined,
        });
        const id = res.data?.task_id;
        if (id) {
          setCurrent(id);
          // Only clear what was sent: a draft typed while the request was in
          // flight must survive.
          setText((prev) => clearedDraft(prev, input));
        } else {
          setSendError(apiErrorText(res));
        }
      } else if (view.parked) {
        // #139: one task = one conversation — speaking to a parked task
        // continues it in place (same thread_id / checkpoint). A 409 (no
        // checkpoint, still shutting down, …) must not eat the message: keep
        // the text and show the server's reason.
        const res = await sendMessage(task.id, input);
        if (res.data?.task) {
          upsert(res.data.task);
          setText((prev) => clearedDraft(prev, input));
        } else {
          setSendError(apiErrorText(res));
        }
      }
    } catch {
      setSendError(`发送失败：${BACKEND_UNREACHABLE}`);
    } finally {
      setBusy(false);
    }
  }, [
    text,
    busy,
    view.inputDisabled,
    view.parked,
    task,
    draftMode,
    draftModel,
    defaultModelId,
    workspaceDir,
    setCurrent,
    upsert,
  ]);

  return { text, setText, busy, sendError, reset, onSubmit };
}
