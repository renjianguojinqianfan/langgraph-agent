import { create } from "zustand";
import { toggleReviewed } from "../lib/changes";

/**
 * In-memory "reviewed" markers (#343). Never persisted or uploaded.
 *
 * The key is ``(task, round)`` (``reviewKey``), so a new round starts empty.
 * ``clearAll`` runs when the selected task changes; both resets are session
 * only, exactly like the contract says.
 */
interface ChangesReviewState {
  /** reviewKey -> reviewed task-relative paths. */
  reviewed: Record<string, string[]>;
  toggle: (key: string, path: string) => void;
  clearAll: () => void;
}

export const useChangesReviewStore = create<ChangesReviewState>((set) => ({
  reviewed: {},
  toggle: (key, path) =>
    set((state) => ({
      reviewed: {
        ...state.reviewed,
        [key]: toggleReviewed(state.reviewed[key] ?? [], path),
      },
    })),
  clearAll: () => set({ reviewed: {} }),
}));
