import { FormEvent, ReactNode, useEffect, useState } from "react";
import {
  apiErrorText,
  createModel,
  deleteModel,
  setDefaultModel,
  setModelKey,
  testModel,
  updateModel,
} from "../api/client";
import { useModelStore } from "../store/modelStore";
import { useTaskStore } from "../store/taskStore";
import { ModelKeySource, ModelProfile } from "../types";

const SOURCE_LABEL: Record<ModelKeySource, string> = {
  pasted: "粘贴",
  env: "环境变量",
  none: "无",
};

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

export function SettingsPage() {
  const models = useModelStore((s) => s.models);
  const defaultId = useModelStore((s) => s.defaultId);
  const loading = useModelStore((s) => s.loading);
  const loadError = useModelStore((s) => s.error);
  const refresh = useModelStore((s) => s.refresh);
  const setOpen = useTaskStore((s) => s.setSettingsOpen);

  const [form, setForm] = useState<FormState | null>(null);
  const [formNote, setFormNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [rowNotes, setRowNotes] = useState<Record<string, string>>({});
  const [armed, setArmed] = useState<string | null>(null);

  useEffect(() => {
    refresh();
  }, [refresh]);

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
    } catch {
      note(id, "保存 Key 失败");
    }
  };

  const clearKey = async (id: string) => {
    try {
      const r = await setModelKey(id, "");
      if (r.data) {
        note(id, r.data.key_set ? "已清除粘贴的 Key，回退到环境变量" : "已清除 Key");
        await refresh();
      } else {
        note(id, apiErrorText(r));
      }
    } catch {
      note(id, "清除 Key 失败");
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
    <div className="fixed inset-0 z-50 bg-slate-950 flex flex-col">
      <header className="px-4 py-3 border-b border-slate-800 flex items-center gap-3">
        <div className="flex-1 font-semibold">模型配置</div>
        <button
          onClick={() => setOpen(false)}
          className="text-xs px-3 py-1 rounded bg-slate-700 hover:bg-slate-600 text-white"
        >
          关闭
        </button>
      </header>

      <div className="flex-1 overflow-auto p-4">
        <div className="max-w-3xl mx-auto space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-slate-500">
              只支持 OpenAI 兼容接口；Key 只写不读，保存在仓库外的
              ~/.qianfan/credentials.json，或取配置里写的环境变量。
            </p>
            <button
              onClick={() => {
                setForm({ ...EMPTY_FORM });
                setFormNote("");
              }}
              className="text-xs px-3 py-1 rounded bg-sky-600 hover:bg-sky-500 text-white"
            >
              新增配置
            </button>
          </div>

          {loadError && <div className="text-[11px] text-red-400">{loadError}</div>}
          {models.length === 0 && (
            <div className="text-sm text-slate-500">
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
              onKeyDraft={(v) => setKeyDrafts((prev) => ({ ...prev, [m.id]: v }))}
              onSaveKey={() => saveKey(m.id)}
              onClearKey={() => clearKey(m.id)}
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
              onArm={() => setArmed(m.id)}
              onCancelArm={() => setArmed(null)}
              onRemove={() => remove(m.id)}
            />
          ))}

          {form && (
            <form
              onSubmit={save}
              className="border border-sky-700 rounded p-3 space-y-2 bg-panel"
            >
              <div className="text-sm font-semibold">
                {form.id ? "编辑配置" : "新增配置"}
              </div>
              <FormField
                label="名称"
                value={form.name}
                onChange={(v) => setForm({ ...form, name: v })}
                placeholder="例如：Bailian qwen"
              />
              <FormField
                label="地址"
                value={form.base_url}
                onChange={(v) => setForm({ ...form, base_url: v })}
                placeholder="https://…/compatible-mode/v1"
              />
              <FormField
                label="模型名"
                value={form.model}
                onChange={(v) => setForm({ ...form, model: v })}
                placeholder="例如：qwen3.8-flash"
              />
              <FormField
                label="环境变量名"
                value={form.api_key_env}
                onChange={(v) => setForm({ ...form, api_key_env: v })}
                placeholder="LLM_API_KEY"
              />
              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={saving}
                  className="text-xs px-3 py-1 rounded bg-sky-600 hover:bg-sky-500 text-white disabled:opacity-40"
                >
                  {saving ? "保存中…" : "保存"}
                </button>
                <button
                  type="button"
                  onClick={() => setForm(null)}
                  className="text-xs px-3 py-1 rounded bg-slate-700 hover:bg-slate-600 text-white"
                >
                  取消
                </button>
                {formNote && <span className="text-[11px] text-red-400">{formNote}</span>}
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

function FormField({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-20 shrink-0 text-slate-400">{label}</span>
      <input
        aria-label={label}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100 focus:outline-none focus:border-sky-500"
      />
    </label>
  );
}

function ModelRow({
  profile,
  isDefault,
  keyDraft,
  note,
  armed,
  onKeyDraft,
  onSaveKey,
  onClearKey,
  onTest,
  onDefault,
  onEdit,
  onArm,
  onCancelArm,
  onRemove,
}: {
  profile: ModelProfile;
  isDefault: boolean;
  keyDraft: string;
  note: string;
  armed: boolean;
  onKeyDraft: (value: string) => void;
  onSaveKey: () => void;
  onClearKey: () => void;
  onTest: () => void;
  onDefault: () => void;
  onEdit: () => void;
  onArm: () => void;
  onCancelArm: () => void;
  onRemove: () => void;
}) {
  return (
    <div
      data-model-id={profile.id}
      className="border border-slate-700 rounded p-3 bg-panel space-y-2"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-semibold text-sm">{profile.name}</span>
        {isDefault && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-600 text-white">默认</span>
        )}
        {profile.preset && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-300">
            预设
          </span>
        )}
        <span className="text-[11px] text-slate-500">
          {profile.model} · {profile.base_url}
        </span>
      </div>
      <div className="text-[11px] text-slate-500">
        环境变量：{profile.api_key_env} · Key：
        {profile.key_set
          ? `已设（${SOURCE_LABEL[profile.key_source] ?? profile.key_source}）`
          : "未设"}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="password"
          aria-label={`粘贴 Key：${profile.name}`}
          placeholder="粘贴 Key（只写不读）"
          value={keyDraft}
          onChange={(e) => onKeyDraft(e.target.value)}
          className="w-56 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-100 focus:outline-none focus:border-sky-500"
        />
        <RowButton onClick={onSaveKey} disabled={!keyDraft.trim()}>
          保存 Key
        </RowButton>
        <RowButton onClick={onClearKey}>清除 Key</RowButton>
        <RowButton onClick={onTest}>测试</RowButton>
        <RowButton onClick={onDefault} disabled={isDefault}>
          设为默认
        </RowButton>
        <RowButton onClick={onEdit}>编辑</RowButton>
        {armed ? (
          <>
            <RowButton onClick={onRemove} danger>
              确认删除
            </RowButton>
            <RowButton onClick={onCancelArm}>取消</RowButton>
          </>
        ) : (
          <RowButton onClick={onArm} danger>
            删除
          </RowButton>
        )}
      </div>
      {note && <div className="text-[11px] text-slate-400">{note}</div>}
    </div>
  );
}

function RowButton({
  children,
  onClick,
  disabled,
  danger,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`text-[11px] px-2 py-1 rounded text-white disabled:opacity-40 ${
        danger ? "bg-red-700 hover:bg-red-600" : "bg-slate-700 hover:bg-slate-600"
      }`}
    >
      {children}
    </button>
  );
}
