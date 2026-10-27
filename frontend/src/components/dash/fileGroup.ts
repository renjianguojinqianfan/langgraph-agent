import type { WorkspaceFile } from "../../types";
import { basename } from "../../lib/format";

export interface FileGroup {
  /** Full directory path; empty means files at the workspace root. */
  dir: string;
  /** Directory basename for the group header; "根目录" in the root group. */
  label: string;
  files: WorkspaceFile[];
}

/** Flat server list grouped by parent directory, first-seen order preserved. */
export function groupFiles(files: WorkspaceFile[]): FileGroup[] {
  const groups: FileGroup[] = [];
  const byDir = new Map<string, FileGroup>();
  for (const file of files) {
    const cut = file.path.lastIndexOf("/");
    const dir = cut > 0 ? file.path.slice(0, cut) : "";
    let group = byDir.get(dir);
    if (!group) {
      group = { dir, label: dir ? basename(dir) : "根目录", files: [] };
      byDir.set(dir, group);
      groups.push(group);
    }
    group.files.push(file);
  }
  return groups;
}

export function fileName(path: string): string {
  return basename(path);
}
