import { useState } from "react";
import { basename } from "../../lib/format";
import { useAuthStore } from "../../store/authStore";
import { useTaskStore } from "../../store/taskStore";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { WorkspaceDialog } from "./WorkspaceDialog";

export function RailFooter() {
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const logout = useAuthStore((s) => s.logout);
  const setSettingsOpen = useTaskStore((s) => s.setSettingsOpen);
  const workspaceDir = useTaskStore((s) => s.workspaceDir);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const workspaceLabel = workspaceDir ? basename(workspaceDir) : "默认目录";

  return (
    <footer className="flex shrink-0 items-center justify-between gap-2 border-t border-rule px-3 py-2">
      <button
        type="button"
        title={workspaceDir || "由后台启动目录决定"}
        aria-label={`切换工作区：${workspaceDir || "默认目录"}`}
        onClick={() => setWorkspaceOpen(true)}
        className="flex min-w-0 items-center gap-1.5 rounded-control px-1.5 py-1 text-meta text-tertiary hover:bg-surface-hover hover:text-primary"
      >
        <Icon name="folder" size={14} />
        <span className="truncate">{workspaceLabel}</span>
      </button>
      <div className="flex shrink-0 items-center gap-1">
        {authEnabled && (
          <Button size="sm" variant="quiet" onClick={logout}>
            登出
          </Button>
        )}
        <IconButton
          size="sm"
          icon="gear"
          aria-label="设置"
          onClick={() => setSettingsOpen(true)}
        />
      </div>
      <WorkspaceDialog open={workspaceOpen} onClose={() => setWorkspaceOpen(false)} />
    </footer>
  );
}
