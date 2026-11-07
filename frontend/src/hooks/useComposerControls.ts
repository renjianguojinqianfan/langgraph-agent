import { useCallback, useState } from "react";
import {
  addProviderModel,
  apiErrorText,
  getTask,
  putMode,
  putTaskModel,
} from "../api/client";
import { BACKEND_UNREACHABLE } from "../lib/copy";
import {
  findEntryByModel,
  modelSwitchFailure,
  modelSwitchSuccess,
} from "../lib/modelCatalog";
import { isStoppable } from "../lib/taskState";
import { useModelStore } from "../store/modelStore";
import { useTaskStore } from "../store/taskStore";
import type { Task, TaskMode } from "../types";

/** D-32 / #232: owns the mode/model drafts and the switch-side APIs.
 * #243 P2-3 adds the catalog-outside model switch: `{provider, model}` on an
 * existing task (the service auto-creates a manual entry), and the equivalent
 * add-then-pick when the Composer has no task yet (the create face takes an
 * entry id only, so the entry is created first). */
export interface UseComposerControlsOptions {
  task: Task | undefined;
}

export function useComposerControls({ task }: UseComposerControlsOptions) {
  const [draftMode, setDraftMode] = useState<TaskMode>("build");
  const [draftModel, setDraftModel] = useState("");
  const [switchNote, setSwitchNote] = useState<string | null>(null);
  const upsert = useTaskStore((s) => s.upsertTask);
  const defaultModelId = useModelStore((s) => s.defaultId);
  const models = useModelStore((s) => s.models);
  const refreshModels = useModelStore((s) => s.refresh);
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
        const r = await putTaskModel(task.id, { model: next });
        if (r.data?.ok) {
          const fresh = await getTask(task.id);
          if (fresh.data) upsert(fresh.data);
          setSwitchNote(modelSwitchSuccess(running));
        } else {
          setSwitchNote(modelSwitchFailure(apiErrorText(r)));
        }
      } catch {
        setSwitchNote(`切换失败：${BACKEND_UNREACHABLE}`);
      }
    },
    [task, running, defaultModelId, upsert]
  );

  /** Catalog-outside model: `{provider, model}` switch on an existing task
   * (D-34 auto-creates a manual entry), or add-then-pick before one exists.
   * Returns true when the picker can close (the switch succeeded). */
  const onCustomModel = useCallback(
    async (providerId: string, rawModel: string): Promise<boolean> => {
      setSwitchNote(null);
      const model = rawModel.trim();
      if (!providerId || !model) return false;

      if (!task) {
        const existing = findEntryByModel(models, providerId, model);
        if (existing) {
          setDraftModel(existing.id);
          setSwitchNote(`新任务将使用 ${existing.name || existing.model}`);
          return true;
        }
        try {
          const r = await addProviderModel(providerId, { model });
          if (r.data) {
            await refreshModels();
            setDraftModel(r.data.id);
            setSwitchNote(`已把「${r.data.model}」加入目录；新任务将使用它`);
            return true;
          }
          setSwitchNote(modelSwitchFailure(apiErrorText(r)));
          return false;
        } catch {
          setSwitchNote(`切换失败：${BACKEND_UNREACHABLE}`);
          return false;
        }
      }

      try {
        const r = await putTaskModel(task.id, { provider: providerId, model });
        if (r.data?.ok) {
          const fresh = await getTask(task.id);
          if (fresh.data) upsert(fresh.data);
          // The on-demand entry is now part of the catalog; the picker should
          // show it as a normal option instead of the raw fallback.
          await refreshModels();
          setSwitchNote(modelSwitchSuccess(running));
          return true;
        }
        setSwitchNote(modelSwitchFailure(apiErrorText(r)));
        return false;
      } catch {
        setSwitchNote(`切换失败：${BACKEND_UNREACHABLE}`);
        return false;
      }
    },
    [task, running, models, upsert, refreshModels]
  );

  return {
    draftMode,
    draftModel,
    switchNote,
    reset,
    onMode,
    onModel,
    onCustomModel,
  };
}
