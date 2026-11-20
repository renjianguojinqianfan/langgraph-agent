import { create } from "zustand";

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
    set((state) => {
      const current = state.reviewed[key] ?? [];
      const next = current.includes(path)
        ? current.filter((item) => item !== path)
        : [...current, path];
      return { reviewed: { ...state.reviewed, [key]: next } };
    }),
  clearAll: () => set({ reviewed: {} }),
}));

export function isReviewed(
  state: Pick<ChangesReviewState, "reviewed">,
  key: string,
  path: string
): boolean {
  return (state.reviewed[key] ?? []).includes(path);
}
