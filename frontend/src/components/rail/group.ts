import type { Task } from "../../types";
import { basename } from "../../lib/format";

export type RailFilter = "current" | "all";

export interface TaskGroup {
  /** Full workspace path; empty means the backend default directory. */
  workspace: string;
  /** Directory basename for the group header; empty in the ungrouped view. */
  label: string;
  tasks: Task[];
}

export function workspaceName(workspace: string): string {
  return basename(workspace) || "默认目录";
}

/** Groups kept in first-seen order; rows inside a group keep the list order. */
export function groupByWorkspace(tasks: Task[]): TaskGroup[] {
  const groups: TaskGroup[] = [];
  const byPath = new Map<string, TaskGroup>();
  for (const task of tasks) {
    const workspace = task.workspace.trim();
    let group = byPath.get(workspace);
    if (!group) {
      group = { workspace, label: workspaceName(workspace), tasks: [] };
      byPath.set(workspace, group);
      groups.push(group);
    }
    group.tasks.push(task);
  }
  return groups;
}

/** "current" is one ungrouped list; "all" splits by workspace. */
export function railGroups(tasks: Task[], filter: RailFilter): TaskGroup[] {
  if (filter === "all") return groupByWorkspace(tasks);
  return [{ workspace: "", label: "", tasks }];
}

/** Search filters the already-loaded list only: title, input, workspace. */
export function filterByQuery(tasks: Task[], query: string): Task[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return tasks;
  return tasks.filter((task) =>
    [task.title, task.user_input, task.workspace].some((value) =>
      value.toLowerCase().includes(needle)
    )
  );
}
