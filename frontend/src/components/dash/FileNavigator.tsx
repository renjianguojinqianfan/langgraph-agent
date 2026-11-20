import {
  useEffect,
  useRef,
  type KeyboardEvent,
  type MutableRefObject,
  type PointerEvent,
  type UIEvent,
} from "react";
import type { WorkspaceFilesState } from "../../hooks/useWorkspaceFiles";
import {
  clampNavigatorWidth,
  navigatorKeyWidth,
  type NavigatorLayout,
} from "../../lib/navigatorLayout";
import type { Task } from "../../types";
import { FileTree } from "./FileTree";

export interface FileNavigatorProps {
  task: Task;
  files: WorkspaceFilesState;
  expanded: ReadonlySet<string>;
  onToggleDir: (path: string) => void;
  query: string;
  onQueryChange: (query: string) => void;
  activePath: string | null;
  onOpenFile: (path: string) => void;
  layout: NavigatorLayout;
  width: number;
  onWidthChange: (width: number) => void;
  focusToken: number;
  /** Task-keyed tree scrollTop so closing/tab-switching does not reset it. */
  scrollMemory: MutableRefObject<Map<string, number>>;
}

/**
 * #346 navigator body: the shared #341 tree plus a width separator. The host
 * (FilePreviewTab) decides whether this sits beside the preview (side) or
 * floats over it (float/fill); the panel itself only owns scrolling and the
 * drag/keyboard resize.
 */
export function FileNavigator({
  task,
  files,
  expanded,
  onToggleDir,
  query,
  onQueryChange,
  activePath,
  onOpenFile,
  layout,
  width,
  onWidthChange,
  focusToken,
  scrollMemory,
}: FileNavigatorProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const restoredFor = useRef<string | null>(null);
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  // Restore only after the first list settles; setting scrollTop against an
  // empty list would clamp to 0 and lose the remembered position.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || files.loading || restoredFor.current === task.id) return;
    const remembered = scrollMemory.current.get(task.id) ?? 0;
    // A remembered position needs rows to scroll; wait for a successful list
    // instead of clamping to 0 on an empty/error first face.
    if (files.files.length === 0 && remembered > 0) return;
    el.scrollTop = remembered;
    restoredFor.current = task.id;
  }, [task.id, files.loading, files.files.length, scrollMemory]);

  const rememberScroll = (event: UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget;
    // A removed node can emit one last scroll after its box collapses; that is
    // not a user scroll and must not erase the remembered position.
    if (el !== scrollRef.current || !el.isConnected) return;
    if (el.scrollTop === 0 && el.clientHeight === 0) return;
    scrollMemory.current.set(task.id, el.scrollTop);
  };

  const onSeparatorKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const next = navigatorKeyWidth(width, event.key, layout);
    if (next === null) return;
    event.preventDefault();
    onWidthChange(next);
  };

  const onSeparatorPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    drag.current = { startX: event.clientX, startWidth: width };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onSeparatorPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const next = drag.current.startWidth + (event.clientX - drag.current.startX);
    onWidthChange(clampNavigatorWidth(next, layout));
  };

  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 w-full">
      <div
        ref={scrollRef}
        onScroll={rememberScroll}
        className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain px-2 py-2"
      >
        <FileTree
          taskId={task.id}
          taskStatus={task.status}
          files={files.files}
          truncated={files.truncated}
          loading={files.loading}
          note={files.note}
          onRetry={files.retry}
          expanded={expanded}
          onToggleDir={onToggleDir}
          activePath={activePath}
          onOpenFile={onOpenFile}
          query={query}
          onQueryChange={onQueryChange}
          scrollContainer={scrollRef}
          focusToken={focusToken}
        />
      </div>

      {layout.resizable && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="调整文件树宽度"
          aria-valuemin={layout.minWidth}
          aria-valuemax={layout.maxWidth}
          aria-valuenow={width}
          tabIndex={0}
          title="拖动或方向键调整文件树宽度"
          onKeyDown={onSeparatorKeyDown}
          onPointerDown={onSeparatorPointerDown}
          onPointerMove={onSeparatorPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onLostPointerCapture={() => {
            drag.current = null;
          }}
          className="group flex w-1.5 shrink-0 cursor-col-resize touch-none items-stretch justify-center"
        >
          <span
            aria-hidden="true"
            className="my-1 w-px rounded-full bg-rule group-hover:bg-rule-strong group-focus-visible:bg-signal-now"
          />
        </div>
      )}
    </div>
  );
}
