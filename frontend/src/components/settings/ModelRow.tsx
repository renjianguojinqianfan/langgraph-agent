import { Button } from "../ui/Button";
import { ModelKeySource, ModelProfile } from "../../types";

export const SOURCE_LABEL: Record<ModelKeySource, string> = {
  pasted: "粘贴",
  env: "环境变量",
  none: "无",
};

interface ModelRowProps {
  profile: ModelProfile;
  isDefault: boolean;
  keyDraft: string;
  note: string;
  /** Delete is armed for this row. */
  armed: boolean;
  /** Clearing the pasted key is armed for this row. */
  keyArmed: boolean;
  onKeyDraft: (value: string) => void;
  onSaveKey: () => void;
  onClearKey: () => void;
  onArmClearKey: () => void;
  onCancelClearKey: () => void;
  onTest: () => void;
  onDefault: () => void;
  onEdit: () => void;
  onArm: () => void;
  onCancelArm: () => void;
  onRemove: () => void;
}

export function ModelRow({
  profile,
  isDefault,
  keyDraft,
  note,
  armed,
  keyArmed,
  onKeyDraft,
  onSaveKey,
  onClearKey,
  onArmClearKey,
  onCancelClearKey,
  onTest,
  onDefault,
  onEdit,
  onArm,
  onCancelArm,
  onRemove,
}: ModelRowProps) {
  return (
    <div
      data-model-id={profile.id}
      className="flex flex-col gap-2 rounded-well border border-rule bg-surface-sheet p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-title text-primary">{profile.name}</span>
        {isDefault && (
          <span className="rounded-full border border-rule-strong px-1.5 py-0.5 text-machine text-primary">
            默认
          </span>
        )}
        {profile.preset && (
          <span className="rounded-full border border-rule px-1.5 py-0.5 text-machine text-tertiary">
            预设
          </span>
        )}
        <span
          className="min-w-0 truncate font-mono text-machine text-tertiary"
          title={`${profile.model} · ${profile.base_url}`}
        >
          {profile.model} · {profile.base_url}
        </span>
      </div>

      <div className="text-meta text-tertiary">
        环境变量：{profile.api_key_env} · Key：
        {profile.key_set
          ? `已设（${SOURCE_LABEL[profile.key_source] ?? profile.key_source}）`
          : "未设"}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <input
          type="password"
          autoComplete="off"
          aria-label={`粘贴 Key：${profile.name}`}
          placeholder="粘贴 Key（只写不读）"
          value={keyDraft}
          onChange={(e) => onKeyDraft(e.target.value)}
          className="h-7 w-56 rounded-control border border-rule bg-surface-well px-2 font-mono text-machine text-primary placeholder:text-tertiary"
        />
        <Button size="sm" onClick={onSaveKey} disabled={!keyDraft.trim()}>
          保存 Key
        </Button>
        {keyArmed ? (
          <>
            <Button size="sm" variant="danger" onClick={onClearKey}>
              确认清除
            </Button>
            <Button size="sm" onClick={onCancelClearKey}>
              取消
            </Button>
          </>
        ) : (
          <Button size="sm" onClick={onArmClearKey}>
            清除 Key
          </Button>
        )}
        <Button size="sm" onClick={onTest}>
          测试
        </Button>
        <Button size="sm" onClick={onDefault} disabled={isDefault}>
          设为默认
        </Button>
        <Button size="sm" onClick={onEdit}>
          编辑
        </Button>
        {armed ? (
          <>
            <Button size="sm" variant="danger" onClick={onRemove}>
              确认删除
            </Button>
            <Button size="sm" onClick={onCancelArm}>
              取消
            </Button>
          </>
        ) : (
          <Button size="sm" variant="danger" onClick={onArm}>
            删除
          </Button>
        )}
      </div>

      {note && (
        <div role="status" className="text-meta text-secondary">
          {note}
        </div>
      )}
    </div>
  );
}
