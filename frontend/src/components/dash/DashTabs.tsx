import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { useElementWidth } from "../../hooks/useElementWidth";
import { useViewportWidth } from "../../hooks/useViewportWidth";
import { useWorkspaceFiles } from "../../hooks/useWorkspaceFiles";
import { cx } from "../../lib/cx";
import {
  changeTabId,
  changeTabRef,
  fileTabId,
  fileTabLabel,
  fileTabPath,
  tabAfterClosing,
  tabAfterClosingChange,
  type DashTab,
} from "../../lib/dashTabs";
import { expandAncestors, toggleDir } from "../../lib/fileTree";
import {
  clampNavigatorWidth,
  navigatorLayout,
} from "../../lib/navigatorLayout";
import {
  navigatorPaneFor,
  updateNavigatorPane,
  type NavigatorPanes,
} from "../../lib/navigatorState";
import type { Task } from "../../types";
import { Button } from "../ui/Button";
import {
  focusTab,
  Tabs,
  tabButtonId,
  tabPanelId,
  type TabItem,
} from "../ui/Tabs";
import { ChangesReviewTab } from "./ChangesReviewTab";
import { EventPane } from "./EventPane";
import { FileNavigator } from "./FileNavigator";
import { FilePane } from "./FilePane";
import { FilePreviewTab, type FilePreviewNavigator } from "./FilePreviewTab";
import { PlanPane } from "./PlanPane";

const TABS: ReadonlyArray<TabItem<DashTab>> = [
  { id: "files", label: "文件" },
  { id: "plan", label: "计划" },
  { id: "events", label: "事件" },
];

const BASE_ID = "dash";

function DashPanel({
  tabId,
  active,
  flush = false,
  children,
}: {
  tabId: DashTab;
  active: boolean;
  /** File previews pad themselves; their navigator needs the panel edge. */
  flush?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      role="tabpanel"
      id={tabPanelId(BASE_ID, tabId)}
      aria-labelledby={tabButtonId(BASE_ID, tabId)}
      hidden={!active}
      className={cx(
        "min-h-0 flex-1 overflow-y-auto overscroll-contain",
        !flush && "px-3 py-3"
      )}
    >
      {children}
    </div>
  );
}

interface DashTabsProps {
  task: Task | null;
  tab: DashTab;
  /** Open file tabs, in the order they were opened (#266). */
  fileTabs: readonly string[];
  /** Open review tabs, one per round, in the order they were opened (#343). */
  changeTabs: readonly number[];
  /** Per-review-tab selected file; the tab itself owns no selection state. */
  changeSelection: Readonly<Record<string, string>>;
  /** #230: false while the narrow-screen drawer is closed — panes must not poll. */
  visible: boolean;
  /** #346: the right column element (border-box), so 420/560 are exact. */
  rowRef?: RefObject<HTMLElement | null>;
  onTabChange: (tab: DashTab) => void;
  onOpenFile: (path: string) => void;
  onCloseFile: (path: string) => void;
  onCloseChange: (round: number) => void;
  onSelectChangeFile: (tabId: string, path: string) => void;
  onOpenTrace: () => void;
}

export function DashTabs({
  task,
  tab,
  fileTabs,
  changeTabs,
  changeSelection,
  visible,
  rowRef,
  onTabChange,
  onOpenFile,
  onCloseFile,
  onCloseChange,
  onSelectChangeFile,
  onOpenTrace,
}: DashTabsProps) {
  // Focus management: opening a file (keyboard or mouse) moves focus from the
  // hidden list row onto its chip; closing returns focus to a neighbour.
  const [focusTarget, setFocusTarget] = useState<DashTab | null>(null);
  const previousTab = useRef<DashTab>(tab);
  useEffect(() => {
    const previous = previousTab.current;
    previousTab.current = tab;
    if (tab !== previous && (fileTabPath(tab) !== null || changeTabRef(tab) !== null)) {
      focusTab(BASE_ID, tab);
    }
  }, [tab]);
  useEffect(() => {
    if (focusTarget === null) return;
    focusTab(BASE_ID, focusTarget);
    setFocusTarget(null);
  }, [focusTarget]);

  // #346: the right-column row drives float/side/fill. Measure the column
  // itself when the host passes it (border-box, so the 420/560 boundaries do
  // not drift by the 1px border); the local root is the fallback/harness.
  const rootRef = useRef<HTMLDivElement>(null);
  const rowWidth = useElementWidth(rowRef ?? rootRef);
  const viewportWidth = useViewportWidth();
  const layout = navigatorLayout(
    rowWidth > 0 ? rowWidth : 420,
    viewportWidth > 0 ? viewportWidth : 1024
  );

  // Session memory per task: open/closed and the raw width the user chose.
  const [navigatorPanes, setNavigatorPanes] = useState<NavigatorPanes>({});
  const taskId = task?.id ?? null;
  const pane = navigatorPaneFor(navigatorPanes, taskId ?? "");
  const navigatorOpen = pane.open;
  const navigatorWidth = clampNavigatorWidth(
    pane.width ?? layout.defaultWidth,
    layout
  );

  const setNavigatorOpen = useCallback(
    (open: boolean) => {
      if (!taskId) return;
      setNavigatorPanes((panes) => updateNavigatorPane(panes, taskId, { open }));
    },
    [taskId]
  );
  const setNavigatorWidth = useCallback(
    (width: number) => {
      if (!taskId) return;
      setNavigatorPanes((panes) =>
        updateNavigatorPane(panes, taskId, {
          width: clampNavigatorWidth(width, layout),
        })
      );
    },
    [taskId, layout]
  );

  const activeFilePath = fileTabPath(tab);

  // One poll for both surfaces. The preview keeps the #230 rule: no request
  // while the right drawer is closed; it also stays quiet until its navigator
  // is actually open, so a closed overlay costs no traffic.
  const files = useWorkspaceFiles(
    task,
    visible && (tab === "files" || (activeFilePath !== null && navigatorOpen))
  );

  // #341 state, lifted so the Files tab and the navigator share expansion and
  // filter. Switching tasks starts clean (same rule as the original pane).
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [query, setQuery] = useState("");
  useEffect(() => {
    setExpanded(new Set());
    setQuery("");
  }, [taskId]);
  useEffect(() => {
    if (!activeFilePath) return;
    setExpanded((current) => expandAncestors(current, activeFilePath));
  }, [activeFilePath, taskId]);
  const toggleDirectory = useCallback((path: string) => {
    setExpanded((current) => toggleDir(current, path));
  }, []);

  // Navigator scroll is per task; a tab switch remounts the panel, so this
  // ref is what keeps the tree where the user left it.
  const treeScroll = useRef(new Map<string, number>());

  const openFile = useCallback(
    (path: string) => {
      // Fill mode is tree-or-preview, never both: opening a file reveals the
      // preview. Float/side keep the navigator open as the user left it.
      if (taskId && layout.mode === "fill") {
        setNavigatorPanes((panes) =>
          updateNavigatorPane(panes, taskId, { open: false })
        );
      }
      onOpenFile(path);
    },
    [layout.mode, onOpenFile, taskId]
  );

  const closeFile = (path: string) => {
    setFocusTarget(tabAfterClosing(fileTabs, path, tab));
    onCloseFile(path);
  };

  const closeChange = (round: number) => {
    setFocusTarget(
      task ? tabAfterClosingChange(changeTabs, round, tab, task.id) : "files"
    );
    onCloseChange(round);
  };

  const tabs: TabItem<DashTab>[] = [
    ...TABS,
    ...fileTabs.map((path) => ({
      id: fileTabId(path),
      label: fileTabLabel(path, fileTabs),
      title: path,
      icon: "file" as const,
      onClose: () => closeFile(path),
    })),
    ...(task
      ? changeTabs.map((round) => ({
          id: changeTabId(task.id, round),
          label: `变更 · 第 ${round} 轮`,
          title: `第 ${round} 轮变更`,
          onClose: () => closeChange(round),
        }))
      : []),
  ];

  const empty = (
    <p className="text-body text-tertiary">
      选择一个任务查看文件、计划与事件。
    </p>
  );

  const navigatorFor = (path: string): FilePreviewNavigator | undefined => {
    if (!task || path !== activeFilePath) return undefined;
    return {
      open: navigatorOpen,
      mode: layout.mode,
      width: navigatorWidth,
      onToggle: () => setNavigatorOpen(!navigatorOpen),
      onClose: () => setNavigatorOpen(false),
      render: (focusToken) => (
        <FileNavigator
          task={task}
          files={files}
          expanded={expanded}
          onToggleDir={toggleDirectory}
          query={query}
          onQueryChange={setQuery}
          activePath={activeFilePath}
          onOpenFile={openFile}
          layout={layout}
          width={navigatorWidth}
          onWidthChange={setNavigatorWidth}
          focusToken={focusToken}
          scrollMemory={treeScroll}
        />
      ),
    };
  };

  return (
    <div ref={rootRef} className="flex h-full min-h-0 flex-col">
      <div className="flex min-w-0 shrink-0 items-center overflow-x-auto border-b border-rule px-2">
        <Tabs
          label="工作区"
          tabs={tabs}
          value={tab}
          onChange={onTabChange}
          baseId={BASE_ID}
        />
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        {TABS.map((t) => {
          const active = t.id === tab;
          if (t.id === "files") {
            // Kept mounted even while a preview tab is active: returning to the
            // list keeps its scroll position instead of re-finding the row.
            return (
              <DashPanel key={t.id} tabId={t.id} active={active}>
                {task ? (
                  <FilePane
                    task={task}
                    files={files}
                    expanded={expanded}
                    onToggleDir={toggleDirectory}
                    query={query}
                    onQueryChange={setQuery}
                    activePath={activeFilePath}
                    onOpenFile={openFile}
                  />
                ) : (
                  empty
                )}
              </DashPanel>
            );
          }
          return (
            <DashPanel key={t.id} tabId={t.id} active={active}>
              {active &&
                (task ? (
                  t.id === "plan" ? (
                    <PlanPane task={task} />
                  ) : (
                    <EventPane task={task} />
                  )
                ) : (
                  empty
                ))}
            </DashPanel>
          );
        })}

        {task &&
          fileTabs.map((path) => {
            const id = fileTabId(path);
            return (
              <DashPanel key={path} tabId={id} active={id === tab} flush>
                <FilePreviewTab
                  taskId={task.id}
                  path={path}
                  active={id === tab}
                  visible={visible}
                  navigator={navigatorFor(path)}
                />
              </DashPanel>
            );
          })}

        {task &&
          changeTabs.map((round) => {
            const id = changeTabId(task.id, round);
            return (
              <DashPanel key={id} tabId={id} active={id === tab}>
                <ChangesReviewTab
                  taskId={task.id}
                  round={round}
                  selectedPath={changeSelection[id] ?? ""}
                  onSelectFile={(path) => onSelectChangeFile(id, path)}
                  onOpenFile={openFile}
                  active={id === tab}
                  visible={visible}
                />
              </DashPanel>
            );
          })}
      </div>

      <div className="shrink-0 border-t border-rule px-3 py-2">
        <Button
          variant="quiet"
          size="sm"
          onClick={onOpenTrace}
          disabled={!task}
        >
          打开完整 Trace
        </Button>
      </div>
    </div>
  );
}
