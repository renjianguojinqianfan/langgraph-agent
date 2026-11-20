import { useEffect, useRef, useState, type ReactNode } from "react";
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
import { FilePane } from "./FilePane";
import { FilePreviewTab } from "./FilePreviewTab";
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
  children,
}: {
  tabId: DashTab;
  active: boolean;
  children: ReactNode;
}) {
  return (
    <div
      role="tabpanel"
      id={tabPanelId(BASE_ID, tabId)}
      aria-labelledby={tabButtonId(BASE_ID, tabId)}
      hidden={!active}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3"
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

  return (
    <div className="flex h-full min-h-0 flex-col">
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
                    visible={visible && active}
                    activePath={fileTabPath(tab)}
                    onOpenFile={onOpenFile}
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
              <DashPanel key={path} tabId={id} active={id === tab}>
                <FilePreviewTab
                  taskId={task.id}
                  path={path}
                  active={id === tab}
                  visible={visible}
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
                  onOpenFile={onOpenFile}
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
