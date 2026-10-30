import { useEffect, useRef, type ReactNode } from "react";
import { fileTabId, fileTabLabel, type DashTab } from "../../lib/dashTabs";
import type { Task } from "../../types";
import { Button } from "../ui/Button";
import {
  Tabs,
  tabButtonId,
  tabPanelId,
  type TabItem,
} from "../ui/Tabs";
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
  /** #230: false while the narrow-screen drawer is closed — panes must not poll. */
  visible: boolean;
  onTabChange: (tab: DashTab) => void;
  onOpenFile: (path: string) => void;
  onCloseFile: (path: string) => void;
  onOpenTrace: () => void;
}

export function DashTabs({
  task,
  tab,
  fileTabs,
  visible,
  onTabChange,
  onOpenFile,
  onCloseFile,
  onOpenTrace,
}: DashTabsProps) {
  // Closing removes the focused button; put focus back on the active tab so a
  // keyboard user is not dropped to <body>.
  const closing = useRef(false);
  const closeFile = (path: string) => {
    closing.current = true;
    onCloseFile(path);
  };
  useEffect(() => {
    if (!closing.current) return;
    closing.current = false;
    const id = tabButtonId(BASE_ID, tab);
    const frame = requestAnimationFrame(() => {
      document.getElementById(id)?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [tab, fileTabs]);

  const tabs: TabItem<DashTab>[] = [
    ...TABS,
    ...fileTabs.map((path) => ({
      id: fileTabId(path),
      label: fileTabLabel(path, fileTabs),
      title: path,
      onClose: () => closeFile(path),
    })),
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
                <FilePreviewTab taskId={task.id} path={path} />
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
