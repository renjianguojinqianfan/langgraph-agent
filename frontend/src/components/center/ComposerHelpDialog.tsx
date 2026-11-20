import type { RefObject } from "react";
import type { SlashCommandItem } from "../../lib/slashCommands";
import { Button } from "../ui/Button";

interface ComposerHelpDialogProps {
  dialogRef: RefObject<HTMLDialogElement>;
  items: SlashCommandItem[];
  onClose: () => void;
}

/**
 * #342: the `/help` dialog. A native `<dialog>` supplies the focus trap, Esc
 * handling and focus return; the composer only owns the open state/ref.
 */
export function ComposerHelpDialog({
  dialogRef,
  items,
  onClose,
}: ComposerHelpDialogProps) {
  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="slash-help-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === dialogRef.current) dialogRef.current?.close();
      }}
      className="m-auto w-[min(480px,calc(100vw-2rem))] rounded-dialog border border-rule bg-surface-column p-0 text-primary backdrop:bg-surface-page/80"
    >
      <div className="flex flex-col gap-3 p-5">
        <div className="flex items-center justify-between gap-2">
          <h2 id="slash-help-title" className="text-title">
            命令
          </h2>
          <Button size="sm" onClick={onClose}>
            关闭
          </Button>
        </div>
        <p className="text-meta text-secondary">
          输入 / 打开菜单；↑/↓ 选择，Enter 或 Tab 执行，Esc
          关闭。执行命令不会发送消息。
        </p>
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li key={item.id} className="flex min-w-0 items-baseline gap-2">
              <code className="shrink-0 font-mono text-machine text-primary">
                {item.label}
              </code>
              <span className="min-w-0 flex-1 text-meta text-secondary">
                {item.description}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </dialog>
  );
}
