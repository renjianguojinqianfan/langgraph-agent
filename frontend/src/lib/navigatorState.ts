/** #346 navigator state is remembered per task/pane for the session (in-memory
 * only, like the rest of the dash tab state). Width is stored raw: the live
 * layout clamps it for the current float/side/fill mode, so narrowing the
 * window never destroys the width the user chose. */
export interface NavigatorPaneState {
  open: boolean;
  /** Raw remembered width, or null until the user resizes the separator. */
  width: number | null;
}

export type NavigatorPanes = Readonly<Record<string, NavigatorPaneState>>;

const EMPTY_PANE: NavigatorPaneState = { open: false, width: null };

export function navigatorPaneFor(
  panes: NavigatorPanes,
  taskId: string
): NavigatorPaneState {
  return panes[taskId] ?? EMPTY_PANE;
}

export function updateNavigatorPane(
  panes: NavigatorPanes,
  taskId: string,
  patch: Partial<NavigatorPaneState>
): NavigatorPanes {
  return {
    ...panes,
    [taskId]: { ...navigatorPaneFor(panes, taskId), ...patch },
  };
}
