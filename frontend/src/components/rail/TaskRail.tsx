import { useState } from "react";
import { useTaskStore } from "../../store/taskStore";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Segmented } from "../ui/Segmented";
import { filterByQuery, railGroups, type RailFilter } from "./group";
import { RailFooter } from "./RailFooter";
import { RailSearch } from "./RailSearch";
import { TaskRow } from "./TaskRow";

const FILTER_OPTIONS: Array<{ value: RailFilter; label: string }> = [
  { value: "current", label: "本目录" },
  { value: "all", label: "全部" },
];

export function TaskRail() {
  const tasks = useTaskStore((s) => s.tasks);
  const listError = useTaskStore((s) => s.listError);
  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const setCurrentTask = useTaskStore((s) => s.setCurrentTask);
  const filter = useTaskStore((s) => s.historyFilter);
  const setFilter = useTaskStore((s) => s.setHistoryFilter);
  const [query, setQuery] = useState("");

  const visible = filterByQuery(tasks, query);
  const groups = railGroups(visible, filter);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-2 px-3 pb-3 pt-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-title text-primary">任务</h2>
          <Button size="sm" variant="quiet" onClick={() => setCurrentTask(null)}>
            <Icon name="plus" size={14} />
            新任务
          </Button>
        </div>
        <RailSearch value={query} onChange={setQuery} />
        <Segmented
          options={FILTER_OPTIONS}
          value={filter}
          onChange={setFilter}
          label="任务范围"
        />
        {listError && (
          <p role="status" className="text-meta text-state-danger">
            {listError}
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-2">
        {visible.length === 0 ? (
          <div className="px-3 py-6">
            <p className="text-body text-secondary">这里还没有任务</p>
            <p className="mt-1 text-meta text-tertiary">
              {query.trim() ? "换个关键词试试。" : "在下方输入开始。"}
            </p>
          </div>
        ) : (
          groups.map((group, index) => (
            <div key={`ws:${group.workspace}`} className={index > 0 ? "mt-2" : ""}>
              {group.label && (
                <div
                  title={group.workspace || "默认目录"}
                  className="flex items-center gap-1.5 px-2.5 pb-1 pt-2 font-mono text-machine text-tertiary"
                >
                  <Icon name="folder" size={12} />
                  <span className="truncate">{group.label}</span>
                </div>
              )}
              <div className="flex flex-col gap-0.5">
                {group.tasks.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    selected={task.id === currentTaskId}
                    showWorkspace={filter === "all"}
                    onSelect={setCurrentTask}
                  />
                ))}
              </div>
            </div>
          ))
        )}
      </div>

      <RailFooter />
    </div>
  );
}
