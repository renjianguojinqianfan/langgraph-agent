import { useCallback, useState } from "react";
import { apiErrorText, getTask, putMode, putTaskModel } from "../api/client";
import { BACKEND_UNREACHABLE } from "../lib/copy";
import { isStoppable } from "../lib/taskState";
import { useModelStore } from "../store/modelStore";
import { useTaskStore } from "../store/taskStore";
import type { Task, TaskMode } from "../types";

/** D-32 / #232: owns the mode/model drafts and the two switch-side APIs.
 * `running` decides the "下一轮生效" hint; the existing task's own mode/model
 * still wins at render time, so the drafts only matter before a task exists. */
export interface UseComposerControlsOptions {
  task: Task | undefined;
}

export function useComposerControls({ task }: UseComposerControlsOptions) {
  const [draftMode, setDraftMode] = useState<TaskMode>("build");
  const [draftModel, setDraftModel] = useState("");
  const [switchNote, setSwitchNote] = useState<string | null>(null);
  const upsert = useTaskStore((s) => s.upsertTask);
  const defaultModelId = useModelStore((s) => s.defaultId);
  const running = task ? isStoppable(task.status) : false;

  const reset = useCallback(() => setSwitchNote(null), []);

  const onMode = useCallback(
    async (next: TaskMode) => {
      setSwitchNote(null);
      if (!task) {
        setDraftMode(next);
        return;
      }
      try {
        const r = await putMode(task.id, next);
        if (r.data?.ok) {
          const fresh = await getTask(task.id);
          if (fresh.data) upsert(fresh.data);
          if (running) setSwitchNote("下一轮生效");
        } else {
          setSwitchNote(apiErrorText(r));
        }
      } catch {
        setSwitchNote(`切换失败：${BACKEND_UNREACHABLE}`);
      }
    },
    [task, running, upsert]
  );

  const onModel = useCallback(
    async (next: string) => {
      setSwitchNote(null);
      if (!task) {
        setDraftModel(next);
        return;
      }
      if (next === (task.model || defaultModelId)) return;
      try {
        const r = await putTaskModel(task.id, next);
        if (r.data?.ok) {
          const fresh = await getTask(task.id);
          if (fresh.data) upsert(fresh.data);
          if (running) setSwitchNote("下一轮生效");
        } else {
          setSwitchNote(apiErrorText(r));
        }
      } catch {
        setSwitchNote(`切换失败：${BACKEND_UNREACHABLE}`);
      }
    },
    [task, running, defaultModelId, upsert]
  );

  return { draftMode, draftModel, switchNote, reset, onMode, onModel };
}
