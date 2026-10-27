import type { Task } from "../../types";
import { Button } from "../ui/Button";
import { Tabs, type TabItem } from "../ui/Tabs";
import { EventPane } from "./EventPane";
import { FilePane } from "./FilePane";
import { PlanPane } from "./PlanPane";

export type DashTab = "files" | "plan" | "events";

const TABS: ReadonlyArray<TabItem<DashTab>> = [
  { id: "files", label: "文件" },
  { id: "plan", label: "计划" },
  { id: "events", label: "事件" },
];

const BASE_ID = "dash";

interface DashTabsProps {
  task: Task | null;
  tab: DashTab;
  onTabChange: (tab: DashTab) => void;
  onOpenTrace: () => void;
}

export function DashTabs({
  task,
  tab,
  onTabChange,
  onOpenTrace,
}: DashTabsProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center border-b border-rule px-2">
        <Tabs
          label="工作区"
          tabs={TABS}
          value={tab}
          onChange={onTabChange}
          baseId={BASE_ID}
        />
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        {TABS.map((t) => (
          <div
            key={t.id}
            role="tabpanel"
            id={`${BASE_ID}-panel-${t.id}`}
            aria-labelledby={`${BASE_ID}-tab-${t.id}`}
            hidden={t.id !== tab}
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3"
          >
            {t.id === tab &&
              (task ? (
                t.id === "files" ? (
                  <FilePane task={task} />
                ) : t.id === "plan" ? (
                  <PlanPane task={task} />
                ) : (
                  <EventPane task={task} />
                )
              ) : (
                <p className="text-body text-tertiary">
                  选择一个任务查看文件、计划与事件。
                </p>
              ))}
          </div>
        ))}
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
