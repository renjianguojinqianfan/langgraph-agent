import { create } from "zustand";
import { apiErrorText, listModels, listProviders } from "../api/client";
import { entriesFromProfiles } from "../lib/modelCatalog";
import { ModelEntry, Provider } from "../types";

/**
 * #243 P2-3: the shared provider + model catalog.
 *
 * Two reads, both compatibility contracts:
 * - `GET /providers` = provider cards (new face, D-34);
 * - `GET /models` = the flat legacy projection, kept as the one-request read
 *   behind the Composer's entry-id → label map (D-34: the old surface is
 *   supported for pre-P2-3 consumers and is not deleted here).
 *
 * Settings and the Composer both refresh through this store so a provider edit
 * immediately changes the picker.
 */
interface ModelState {
  providers: Provider[];
  /** Flat catalog entries; `Task.model` holds one of these ids. */
  models: ModelEntry[];
  /** Global default model entry id. */
  defaultId: string;
  loading: boolean;
  /** Empty when the last refresh succeeded. */
  error: string;
  /** Reload providers + the flat catalog (keys never travel). */
  refresh: () => Promise<void>;
}

export const useModelStore = create<ModelState>((set) => {
  // Monotonic request id: a slow earlier refresh (open dialog) must not
  // overwrite a newer answer (post-switch refresh from the Composer) when the
  // two finish out of order (#299 LOW).
  let refreshSeq = 0;

  return {
    providers: [],
    models: [],
    defaultId: "",
    loading: false,
    error: "",
    refresh: async () => {
      const seq = ++refreshSeq;
      set({ loading: true });
      try {
        const [providersResult, modelsResult] = await Promise.all([
          listProviders(),
          listModels(),
        ]);
        if (seq !== refreshSeq) return; // superseded by a newer refresh
        if (providersResult.data && modelsResult.data) {
          set({
            providers: providersResult.data.providers ?? [],
            models: entriesFromProfiles(modelsResult.data.models ?? []),
            defaultId: providersResult.data.default || modelsResult.data.default || "",
            error: "",
          });
        } else {
          // Name which read failed: the provider list and the flat catalog are
          // separate faces, and a silent empty page is not a usable failure.
          const failed = providersResult.data ? modelsResult : providersResult;
          const detail = apiErrorText(failed);
          set({
            error:
              detail === "操作失败"
                ? "加载模型配置失败"
                : `加载模型配置失败：${detail}`,
          });
        }
      } catch {
        // Keep the last snapshot; the settings page shows the failure note.
        if (seq === refreshSeq) {
          set({ error: "加载模型配置失败：无法连接后台；确认服务在运行后再试。" });
        }
      } finally {
        if (seq === refreshSeq) set({ loading: false });
      }
    },
  };
});
