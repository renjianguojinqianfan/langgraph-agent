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
 *
 * #308 M2: one request generation covers *both* faces. A save invalidates every
 * GET that started before it (and every GET that was still in flight when it
 * finished), so a slow refresh can never overwrite the PUT answer; a GET that
 * resolves while a save is in flight is dropped as well.
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
  /** Monotonic generation shared by refresh and save (see the M2 note above). */
  let requestGen = 0;

  return {
    data: null,
    loading: false,
    error: "",
    saving: false,
    saveError: "",
    saveNote: "",
    refresh: async () => {
      // A save owns the snapshot while in flight; don't even start a competing
      // GET (the page disables its retry, this is the store-side guarantee).
      if (get().saving) return;
      const gen = ++requestGen;
      set({ loading: true });
      try {
        const result = await getPlugins();
        if (gen !== requestGen) return; // superseded by a newer request
        if (get().saving) return; // a save started meanwhile: its answer governs
        if (result.data) {
          set({ data: result.data, error: "" });
        } else {
          set({ error: loadFailure(apiErrorText(result)) });
        }
      } catch {
        if (gen === requestGen) {
          set({ error: "加载能力配置失败：无法连接后台；确认服务在运行后再试。" });
        }
      } finally {
        if (gen === requestGen) set({ loading: false });
      }
    },
    save: async (body) => {
      if (get().saving) return false; // in-flight guard (double click / Enter)
      // Invalidate GETs that started before this save; the PUT answer is newer
      // by definition and the running face must not jump back to an old read.
      requestGen += 1;
      set({ saving: true, loading: false, saveError: "", saveNote: "" });
      try {
        const result = await putPlugins(body);
        // Also invalidate GETs that started during the PUT.
        requestGen += 1;
        if (result.data) {
          set({
            data: result.data,
            // #308 L4: a stale load error must not keep showing after success.
            error: "",
            saveNote: "能力配置已保存。",
          });
          return true;
        }
        set({ saveError: saveFailure(apiErrorText(result)) });
        return false;
      } catch {
        requestGen += 1;
        set({ saveError: "保存失败：无法连接后台；确认服务在运行后再试。" });
        return false;
      } finally {
        set({ saving: false });
      }
    },
    clearSaveNotes: () => set({ saveError: "", saveNote: "" }),
  };
});
