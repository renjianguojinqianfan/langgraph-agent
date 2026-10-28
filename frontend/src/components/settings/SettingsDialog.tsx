import { FormEvent, useEffect, useRef, useState } from "react";
import {
  apiErrorText,
  createModel,
  deleteModel,
  setDefaultModel,
  setModelKey,
  testModel,
  updateModel,
} from "../../api/client";
import { useModelStore } from "../../store/modelStore";
import { Button, IconButton } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { FormField } from "./FormField";
import { ModelRow, SOURCE_LABEL } from "./ModelRow";

interface FormState {
  id?: string;
  name: string;
  base_url: string;
  model: string;
  api_key_env: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  base_url: "",
  model: "",
  api_key_env: "LLM_API_KEY",
};

interface SettingsDialogProps {
  open: boolean;
  onClose: () => void;
}

/** Network/transport failures carry no server envelope; keep the cause visible. */
function requestFailure(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? `${fallback}：${err.message}` : fallback;
}

/**
 * Modal settings surface (design §4 SettingsDialog). Behaviour is the retired
 * `SettingsPage` verbatim: model CRUD, write-only keys, connection test and
 * confirm-before-delete / confirm-before-clearing-a-key. The native <dialog>
 * owns focus containment, Esc and focus restoration; the trigger stays mounted
 * so the browser can return focus to it.
 */
export function SettingsDialog({ open, onClose }: SettingsDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const models = useModelStore((s) => s.models);
  const defaultId = useModelStore((s) => s.defaultId);
  const loading = useModelStore((s) => s.loading);
  const loadError = useModelStore((s) => s.error);
  const refresh = useModelStore((s) => s.refresh);

  const [form, setForm] = useState<FormState | null>(null);
  const [formNote, setFormNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [rowNotes, setRowNotes] = useState<Record<string, string>>({});
  const [armed, setArmed] = useState<string | null>(null);
  const [keyArmed, setKeyArmed] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  // The old page unmounted on close; keep that fresh-on-reopen behaviour now
  // that the dialog stays mounted with the rest of the app.
  const reset = () => {
    setForm(null);
    setFormNote("");
    setSaving(false);
    setKeyDrafts({});
    setRowNotes({});
    setArmed(null);
    setKeyArmed(null);
  };

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  const handleClose = () => {
    reset();
    onClose();
  };

  const note = (id: string, text: string) =>
    setRowNotes((prev) => ({ ...prev, [id]: text }));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setFormNote("");
    const body = {
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      api_key_env: form.api_key_env.trim() || "LLM_API_KEY",
    };
    setSaving(true);
    try {
      const r = form.id ? await updateModel(form.id, body) : await createModel(body);
      if (r.data) {
        setForm(null);
        await refresh();
      } else {
        setFormNote(apiErrorText(r));
      }
    } catch {
      setFormNote("保存失败");
    } finally {
      setSaving(false);
    }
  };

  const saveKey = async (id: string) => {
    const key = keyDrafts[id] ?? "";
    if (!key.trim()) return;
    try {
      const r = await setModelKey(id, key);
      if (r.data) {
        // Only clear what was sent: a newer draft typed while the request was
        // in flight must survive the stale reply.
        setKeyDrafts((prev) =>
          prev[id] === key ? { ...prev, [id]: "" } : prev
        );
        note(id, `Key 已保存（${SOURCE_LABEL[r.data.key_source] ?? r.data.key_source}）`);
        await refresh();
      } else {
        note(id, apiErrorText(r));
      }
    } catch (err) {
      note(id, requestFailure(err, "保存 Key 失败"));
    }
  };

  const clearKey = async (id: string) => {
    setKeyArmed(null);
    try {
      const r = await setModelKey(id, "");
      if (r.data) {
        note(id, r.data.key_set ? "已清除粘贴的 Key，回退到环境变量" : "已清除 Key");
        await refresh();
      } else {
        note(id, apiErrorText(r));
      }
    } catch (err) {
      note(id, requestFailure(err, "清除 Key 失败"));
    }
  };

  const test = async (id: string) => {
    note(id, "测试中…");
    try {
      const r = await testModel(id);
      if (r.data?.ok) note(id, r.data.note ?? "连接正常");
      else note(id, r.data?.error ?? apiErrorText(r) ?? "测试失败");
    } catch {
      note(id, "测试失败");
    }
  };

  const makeDefault = async (id: string) => {
    try {
      const r = await setDefaultModel(id);
      if (r.data) {
        note(id, "已设为默认");
        await refresh();
      } else {
        note(id, apiErrorText(r));
      }
    } catch {
      note(id, "设置默认失败");
    }
  };

  const remove = async (id: string) => {
    setArmed(null);
    try {
      const r = await deleteModel(id);
      if (r.data) {
        await refresh();
      } else {
        note(id, apiErrorText(r));
      }
    } catch {
      note(id, "删除失败");
    }
  };

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="settings-dialog-title"
      onClose={handleClose}
      onClick={(e) => {
        // Clicks land on the dialog itself only outside its content box
        // (padding is 0), i.e. on the backdrop.
        if (e.target === dialogRef.current) dialogRef.current?.close();
      }}
      className="m-auto w-[min(760px,calc(100vw-2rem))] max-h-[min(85vh,calc(100dvh-2rem))] rounded-dialog border border-rule bg-surface-column p-0 text-primary backdrop:bg-surface-page/80"
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
            <p className="max-w-[48ch] text-body text-tertiary">
              只支持 OpenAI 兼容接口；Key 只写不读，保存在仓库外的
              ~/.qianfan/credentials.json，或取配置里写的环境变量。
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setForm({ ...EMPTY_FORM });
                setFormNote("");
              }}
            >
              <Icon name="plus" size={14} />
              新增配置
            </Button>
          </div>

          {loadError && (
            <div role="alert" className="text-meta text-state-danger">
              {loadError}
            </div>
          )}
          {models.length === 0 && (
            <div className="text-body text-tertiary">
              {loading ? "加载中…" : "暂无配置，先新增一套。"}
            </div>
          )}

          {models.map((m) => (
            <ModelRow
              key={m.id}
              profile={m}
              isDefault={m.id === defaultId}
              keyDraft={keyDrafts[m.id] ?? ""}
              note={rowNotes[m.id] ?? ""}
              armed={armed === m.id}
              keyArmed={keyArmed === m.id}
              onKeyDraft={(v) => setKeyDrafts((prev) => ({ ...prev, [m.id]: v }))}
              onSaveKey={() => saveKey(m.id)}
              onClearKey={() => clearKey(m.id)}
              onArmClearKey={() => {
                setKeyArmed(m.id);
                setArmed(null);
              }}
              onCancelClearKey={() => setKeyArmed(null)}
              onTest={() => test(m.id)}
              onDefault={() => makeDefault(m.id)}
              onEdit={() => {
                setForm({
                  id: m.id,
                  name: m.name,
                  base_url: m.base_url,
                  model: m.model,
                  api_key_env: m.api_key_env,
                });
                setFormNote("");
              }}
              onArm={() => {
                setArmed(m.id);
                setKeyArmed(null);
              }}
              onCancelArm={() => setArmed(null)}
              onRemove={() => remove(m.id)}
            />
          ))}

          {form && (
            <form
              onSubmit={save}
              className="flex flex-col gap-2 rounded-well border border-rule-strong bg-surface-sheet p-3"
            >
              <div className="text-body font-semibold text-primary">
                {form.id ? "编辑配置" : "新增配置"}
              </div>
              <FormField
                label="名称"
                name="name"
                value={form.name}
                onChange={(v) => setForm({ ...form, name: v })}
                placeholder="例如：Bailian qwen"
              />
              <FormField
                label="地址"
                name="base_url"
                value={form.base_url}
                onChange={(v) => setForm({ ...form, base_url: v })}
                placeholder="https://…/compatible-mode/v1"
              />
              <FormField
                label="模型名"
                name="model"
                value={form.model}
                onChange={(v) => setForm({ ...form, model: v })}
                placeholder="例如：qwen3.8-flash"
              />
              <FormField
                label="环境变量名"
                name="api_key_env"
                value={form.api_key_env}
                onChange={(v) => setForm({ ...form, api_key_env: v })}
                placeholder="LLM_API_KEY"
              />
              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" variant="outline" size="sm" disabled={saving}>
                  {saving ? "保存中…" : "保存"}
                </Button>
                <Button size="sm" onClick={() => setForm(null)}>
                  取消
                </Button>
                {formNote && (
                  <span role="alert" className="text-meta text-state-danger">
                    {formNote}
                  </span>
                )}
              </div>
            </form>
          )}
        </div>
      </div>
    </dialog>
  );
}
