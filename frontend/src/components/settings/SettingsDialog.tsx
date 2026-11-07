import { useEffect, useRef, useState } from "react";
import { groupByProvider } from "../../lib/modelCatalog";
import { useModelStore } from "../../store/modelStore";
import type { Provider } from "../../types";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { ProviderCard } from "./ProviderCard";
import { ProviderDialog, type ProviderDialogMode } from "./ProviderDialog";

interface SettingsDialogProps {
  open: boolean;
  onClose: () => void;
}

type Editor =
  | { mode: Extract<ProviderDialogMode, "create"> }
  | { mode: Extract<ProviderDialogMode, "edit">; provider: Provider }
  | null;

/**
 * Modal settings surface (design §4 SettingsDialog).
 *
 * #243 P2-3 / D-37: the page is Provider-organised now — one card per API host
 * (name / address / protocol / key state), its model catalog (discovery preview,
 * manual add, delete), and an add/edit dialog with the two user-story tabs.
 * Keys stay write-only; delete of a provider asks for confirmation first, and a
 * model entry's 测试 keeps the pre-existing connectivity check reachable.
 */
export function SettingsDialog({ open, onClose }: SettingsDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const providers = useModelStore((s) => s.providers);
  const models = useModelStore((s) => s.models);
  const defaultId = useModelStore((s) => s.defaultId);
  const loading = useModelStore((s) => s.loading);
  const loadError = useModelStore((s) => s.error);
  const refresh = useModelStore((s) => s.refresh);
  const [editor, setEditor] = useState<Editor>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  // The old page unmounted on close; keep that fresh-on-reopen behaviour now
  // that the dialog stays mounted with the rest of the app.
  useEffect(() => {
    if (open) {
      setEditor(null);
      void refresh();
    }
  }, [open, refresh]);

  const handleClose = () => {
    setEditor(null);
    onClose();
  };

  const groups = groupByProvider(providers, models);

  return (
    <>
      <dialog
        ref={dialogRef}
        aria-labelledby="settings-dialog-title"
        onClose={handleClose}
        onClick={(e) => {
          // Clicks land on the dialog itself only outside its content box
          // (padding is 0), i.e. on the backdrop.
          if (e.target === dialogRef.current) dialogRef.current?.close();
        }}
        className="m-auto w-[min(820px,calc(100vw-2rem))] max-h-[min(85vh,calc(100dvh-2rem))] rounded-dialog border border-rule bg-surface-column p-0 text-primary backdrop:bg-surface-page/80"
      >
        <div className="flex max-h-[inherit] flex-col">
          <header className="flex items-center gap-3 border-b border-rule px-5 py-4">
            <h2 id="settings-dialog-title" className="flex-1 text-title">
              设置
            </h2>
            <IconButton
              icon="close"
              aria-label="关闭设置"
              onClick={() => dialogRef.current?.close()}
            />
          </header>

          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-5 py-4">
            <div className="flex items-start justify-between gap-4">
              <p className="max-w-[56ch] text-body text-tertiary">
                模型配置按 Provider 组织：一个地址 + 一种协议下挂多个模型。
                Key 只写不读，保存在仓库外的 ~/.qianfan/credentials.json，或取配置里写的环境变量。
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditor({ mode: "create" })}
              >
                <Icon name="plus" size={14} />
                添加模型提供商
              </Button>
            </div>

            {loadError && (
              <div role="alert" className="text-meta text-state-danger">
                {loadError}
              </div>
            )}
            {providers.length === 0 && (
              <div className="text-body text-tertiary">
                {loading ? "加载中…" : "暂无 Provider；先添加一个 API 地址。"}
              </div>
            )}

            {groups.map((group) => (
              <ProviderCard
                key={group.provider.id}
                provider={group.provider}
                models={group.models}
                defaultId={defaultId}
                onEdit={() => setEditor({ mode: "edit", provider: group.provider })}
                onRefresh={refresh}
              />
            ))}
          </div>
        </div>
      </dialog>

      <ProviderDialog
        open={editor !== null}
        mode={editor?.mode ?? "create"}
        provider={editor?.mode === "edit" ? editor.provider : undefined}
        providers={providers}
        onClose={() => setEditor(null)}
        onSaved={refresh}
      />
    </>
  );
}
