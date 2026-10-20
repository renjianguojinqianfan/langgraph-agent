import { create } from "zustand";
import { listModels } from "../api/client";
import { ModelProfile } from "../types";

interface ModelState {
  models: ModelProfile[];
  defaultId: string;
  loading: boolean;
  /** Reload the public list (keys never travel). */
  refresh: () => Promise<void>;
}

export const useModelStore = create<ModelState>((set) => ({
  models: [],
  defaultId: "",
  loading: false,
  refresh: async () => {
    set({ loading: true });
    try {
      const r = await listModels();
      if (r.data) {
        set({ models: r.data.models ?? [], defaultId: r.data.default ?? "" });
      }
    } catch {
      // Keep the last snapshot; the caller shows its own failure note.
    } finally {
      set({ loading: false });
    }
  },
}));

/** Display name for a profile id; empty means the global default. */
export function profileName(models: ModelProfile[], id: string): string {
  if (!id) return "默认";
  const found = models.find((m) => m.id === id);
  return found ? found.name : id;
}
