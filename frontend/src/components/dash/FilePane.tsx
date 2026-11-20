import { useEffect, useRef } from "react";
import type { WorkspaceFilesState } from "../../hooks/useWorkspaceFiles";
import type { Task } from "../../types";
import { FileTree } from "./FileTree";

interface FilePaneProps {
  task: Task;
  files: WorkspaceFilesState;
  expanded: ReadonlySet<string>;
  onToggleDir: (path: string) => void;
  query: string;
  onQueryChange: (query: string) => void;
  /** The file whose preview tab is active; its ancestors stay expanded. */
  activePath: string | null;
  onOpenFile: (path: string) => void;
}

/**
 * #341 Files tab. Polling and expansion now live in DashTabs so the #346
 * navigator can reuse the same list and state; this wrapper only owns the
 * tab-panel scroll reset that #341 attached to a task switch.
 */
export function FilePane({
  task,
  files,
  expanded,
  onToggleDir,
  query,
  onQueryChange,
  activePath,
  onOpenFile,
}: FilePaneProps) {
  const paneRef = useRef<HTMLDivElement>(null);

  // #341: switching tasks resets scroll. The scroll owner is the tab panel
  // that keeps this list mounted, not this component.
  useEffect(() => {
    paneRef.current
      ?.closest<HTMLElement>('[role="tabpanel"]')
      ?.scrollTo({ top: 0 });
  }, [task.id]);

  return (
    <div ref={paneRef} className="flex min-w-0 flex-col">
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
      />
    </div>
  );
}
