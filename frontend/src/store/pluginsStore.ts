import { create } from "zustand";
import { apiErrorText, getPlugins, putPlugins } from "../api/client";
import type { PluginsData, PluginsSaveRequest } from "../types";

/**
 * #67 P4-2: the settings page "能力" section's server state.
 *
 * GET carries everything the page needs: the preset/package manifest, the saved
 * file and the backend's active face. Saving replaces the whole snapshot with
 * the PUT response (which already carries the new `restart_required`) — a
 * failure keeps the previous snapshot and surfaces `saveError`. `saving` is
 * the in-flight guard: the page disables toggles / the save button, and a
 * second save during one flight is refused here as well.
 */
interface PluginsState {
  data: PluginsData | null;
  loading: boolean;
  /** Non-empty when the last GET failed (old snapshot stays usable). */
  error: string;
  saving: boolean;
  saveError: string;
  saveNote: string;
  refresh: () => Promise<void>;
  /** Write the file; returns true when the PUT answered with a valid snapshot. */
  save: (body: PluginsSaveRequest) => Promise<boolean>;
  /** Drop the last save note when the draft changes again. */
  clearSaveNotes: () => void;
}

function loadFailure(detail: string): string {
  return detail && detail !== "操作失败"
    ? `加载能力配置失败：${detail}`
    : "加载能力配置失败";
}

function saveFailure(detail: string): string {
  return detail && detail !== "操作失败" ? `保存失败：${detail}` : "保存失败";
}

export const usePluginsStore = create<PluginsState>((set, get) => {
  // Monotonic request id: a slow earlier GET must not overwrite a newer one.
  let refreshSeq = 0;

  return {
    data: null,
    loading: false,
    error: "",
    saving: false,
    saveError: "",
    saveNote: "",
    refresh: async () => {
      const seq = ++refreshSeq;
      set({ loading: true });
      try {
        const result = await getPlugins();
        if (seq !== refreshSeq) return; // superseded by a newer refresh
        if (result.data) {
          set({ data: result.data, error: "" });
        } else {
          set({ error: loadFailure(apiErrorText(result)) });
        }
      } catch {
        if (seq === refreshSeq) {
          set({ error: "加载能力配置失败：无法连接后台；确认服务在运行后再试。" });
        }
      } finally {
        if (seq === refreshSeq) set({ loading: false });
      }
    },
    save: async (body) => {
      if (get().saving) return false; // in-flight guard (double click / Enter)
      set({ saving: true, saveError: "", saveNote: "" });
      try {
        const result = await putPlugins(body);
        if (result.data) {
          set({ data: result.data, saveNote: "能力配置已保存。" });
          return true;
        }
        set({ saveError: saveFailure(apiErrorText(result)) });
        return false;
      } catch {
        set({ saveError: "保存失败：无法连接后台；确认服务在运行后再试。" });
        return false;
      } finally {
        set({ saving: false });
      }
    },
    clearSaveNotes: () => set({ saveError: "", saveNote: "" }),
  };
});
