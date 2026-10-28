import { FormEvent, useEffect, useRef, useState } from "react";
import { apiErrorText, validateWorkspace } from "../../api/client";
import { basename } from "../../lib/format";
import { readRecentWorkspaces, rememberWorkspace } from "../../lib/workspaces";
import { useTaskStore } from "../../store/taskStore";
import { FormField } from "../settings/FormField";
import { Button, IconButton } from "../ui/Button";

interface WorkspaceDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * #242: switch the page's workspace without restarting the backend.
 *
 * Path input + recent list, no server-side directory browsing: the backend only
 * canonicalizes and checks that the directory exists (read-only).
 */
export function WorkspaceDialog({ open, onClose }: WorkspaceDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const workspaceDir = useTaskStore((s) => s.workspaceDir);
  const switchWorkspace = useTaskStore((s) => s.switchWorkspace);
  const [draft, setDraft] = useState(workspaceDir);
  const [recent, setRecent] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  // Fresh state on every open; the current directory may have changed meanwhile.
  useEffect(() => {
    if (!open) return;
    setDraft(workspaceDir);
    setNote("");
    setBusy(false);
    setRecent(readRecentWorkspaces());
  }, [open, workspaceDir]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const target = draft.trim();
    if (!target || busy) return;
    setBusy(true);
    setNote("");
    try {
      const response = await validateWorkspace(target);
      const path = response.data?.path;
      if (!path) {
        setNote(apiErrorText(response));
        return;
      }
      setRecent(rememberWorkspace(path));
      switchWorkspace(path);
      dialogRef.current?.close();
    } catch {
      setNote("无法连接后台；确认 qianfan 在运行。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="workspace-dialog-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === dialogRef.current) dialogRef.current?.close();
      }}
      className="m-auto w-[min(560px,calc(100vw-2rem))] rounded-dialog border border-rule bg-surface-column p-0 text-primary backdrop:bg-surface-page/80"
    >
      <form onSubmit={submit} className="flex flex-col">
        <header className="flex items-center gap-3 border-b border-rule px-5 py-4">
          <h2 id="workspace-dialog-title" className="flex-1 text-title">
            切换工作区
          </h2>
          <IconButton
            icon="close"
            aria-label="关闭工作区选择"
            onClick={() => dialogRef.current?.close()}
          />
        </header>

        <div className="flex flex-col gap-3 px-5 py-4">
          <p className="max-w-[52ch] text-body text-tertiary">
            输入一个已存在的本地目录；只切换历史与新建任务的落点，不重启后台。
          </p>
          <FormField
            label="目录"
            name="workspace"
            value={draft}
            placeholder="例如：E:\\code\\demo\\project"
            onChange={setDraft}
            autoFocus
          />
          {recent.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="text-meta text-tertiary">最近目录</span>
              <div className="flex flex-wrap gap-1">
                {recent.map((path) => (
                  <button
                    key={path}
                    type="button"
                    title={path}
                    onClick={() => setDraft(path)}
                    className="max-w-full truncate rounded-full border border-rule px-2 py-0.5 font-mono text-machine text-secondary hover:bg-surface-hover hover:text-primary"
                  >
                    {basename(path) || path}
                  </button>
                ))}
              </div>
            </div>
          )}
          {note && (
            <p role="alert" className="text-meta text-state-danger">
              {note}
            </p>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-rule px-5 py-3">
          <Button size="sm" onClick={() => dialogRef.current?.close()}>
            取消
          </Button>
          <Button
            type="submit"
            size="sm"
            variant="primary"
            disabled={busy || !draft.trim()}
          >
            {busy ? "校验中…" : "切换"}
          </Button>
        </footer>
      </form>
    </dialog>
  );
}
