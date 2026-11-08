import { create } from "zustand";
import { apiErrorText, listProviderModels, listProviders } from "../api/client";
import { ModelEntry, Provider } from "../types";

/**
 * #243 P2-3 / #267 P2-4b: the shared provider + model catalog.
 *
 * Reads the v2 faces only: `GET /providers` for provider cards (their D-36
 * defaults included) and `GET /providers/{id}/models` for each catalog. The
 * per-provider catalog is used instead of the legacy flat `/api/models`
 * projection because D-36 parameter editing needs each entry's *raw* overrides
 * (null = inherit), while the projection carries the effective values only.
 *
 * Settings and the Composer share this store, so a provider/param edit
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
  /** Reload providers + their raw model catalogs (keys never travel). */
  refresh: () => Promise<void>;
}

function loadFailure(detail: string): string {
  return detail && detail !== "操作失败"
    ? `加载模型配置失败：${detail}`
    : "加载模型配置失败";
}

export const useModelStore = create<ModelState>((set) => {
  // Monotonic request id: a slow earlier refresh (open dialog) must not
  // overwrite a newer answer (post-edit refresh) when they finish out of order.
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
        const providersResult = await listProviders();
        if (seq !== refreshSeq) return; // superseded by a newer refresh
        if (!providersResult.data) {
          set({ error: loadFailure(apiErrorText(providersResult)) });
          return;
        }
        const providers = providersResult.data.providers ?? [];
        const catalogs = await Promise.all(
          providers.map((provider) => listProviderModels(provider.id))
        );
        if (seq !== refreshSeq) return;
        const failedIndex = catalogs.findIndex((catalog) => !catalog.data);
        if (failedIndex >= 0) {
          // Name the failing Provider and say what survived: one broken catalog
          // must not read as "all settings are empty" (#300 L5).
          const failedProvider = providers[failedIndex];
          const label = failedProvider
            ? `${failedProvider.name || failedProvider.id}（${failedProvider.id}）`
            : "未知 Provider";
          set({
            error: `${loadFailure(apiErrorText(catalogs[failedIndex]))}；Provider「${label}」的模型目录读取失败，其余 Provider 保持旧快照。`,
          });
          return;
        }
        const models: ModelEntry[] = [];
        for (const catalog of catalogs) {
          models.push(...(catalog.data?.models ?? []));
        }
        set({
          providers,
          models,
          defaultId: providersResult.data.default || "",
          error: "",
        });
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
