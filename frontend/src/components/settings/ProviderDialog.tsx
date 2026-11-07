import { FormEvent, useEffect, useRef, useState } from "react";
import { apiErrorText, createProvider, updateProvider } from "../../api/client";
import {
  firstFormError,
  isModelProtocol,
  KEY_SOURCE_LABEL,
  PROTOCOL_OPTIONS,
  protocolLabel,
  providerUpdateBody,
  validateProviderForm,
  type ProviderFormErrors,
  type ProviderFormValues,
} from "../../lib/modelCatalog";
import type { Provider } from "../../types";
import { Button, IconButton } from "../ui/Button";
import { Tabs, tabButtonId, tabPanelId } from "../ui/Tabs";
import { FormField } from "./FormField";

export type ProviderDialogMode = "create" | "edit";

interface ProviderDialogProps {
  open: boolean;
  mode: ProviderDialogMode;
  /** The card being edited; absent on create. */
  provider?: Provider;
  /** Current cards: preset templates (create) and duplicate-id checks. */
  providers: Provider[];
  onClose: () => void;
  /** Persist done: the parent reloads the shared provider + catalog store. */
  onSaved: () => Promise<void>;
}

type DialogTab = "preset" | "custom";

function emptyValues(): ProviderFormValues {
  return {
    id: "",
    name: "",
    protocol: "openai-compatible",
    base_url: "",
    api_key_env: "LLM_API_KEY",
  };
}

/**
 * Create/edit one Provider (#243 P2-3 / D-37).
 *
 * Create offers the two tabs from the user story: preset-backed third-party
 * suppliers (only when the backend actually reports `preset` cards — the
 * service has no separate preset catalog, so the page never invents one) and a
 * fully custom API. Edit keeps the id immutable and never re-sends an unchanged
 * protocol: an existing anthropic card must stay editable for its other fields,
 * and re-sending the protocol would re-run the protocol-specific parameter
 * checks (Anthropic rejects thinking / reasoning_effort at the write face).
 */
export function ProviderDialog({
  open,
  mode,
  provider,
  providers,
  onClose,
  onSaved,
}: ProviderDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<DialogTab>("custom");
  const [presetId, setPresetId] = useState("");
  const [values, setValues] = useState<ProviderFormValues>(emptyValues);
  const [keyDraft, setKeyDraft] = useState("");
  const [errors, setErrors] = useState<ProviderFormErrors>({});
  const [note, setNote] = useState<{ kind: "ok" | "error"; text: string } | null>(
    null
  );
  const [saving, setSaving] = useState(false);
  const [clearArmed, setClearArmed] = useState(false);

  const presets = providers.filter(
    (candidate) => candidate.preset && candidate.id !== provider?.id
  );

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  // Fresh form on every open. Keyed by id (not the object): a store refresh
  // while the dialog is open must not discard what the user is typing.
  useEffect(() => {
    if (!open) return;
    setValues(
      provider
        ? {
            id: provider.id,
            name: provider.name,
            protocol: provider.protocol,
            base_url: provider.base_url,
            api_key_env: provider.api_key_env,
          }
        : emptyValues()
    );
    setTab(providers.some((candidate) => candidate.preset) ? "preset" : "custom");
    setPresetId("");
    setKeyDraft("");
    setErrors({});
    setNote(null);
    setSaving(false);
    setClearArmed(false);
  }, [open, provider?.id]);

  const applyPreset = (preset: Provider) => {
    setPresetId(preset.id);
    setErrors({});
    setValues((prev) => ({
      ...prev,
      protocol: preset.protocol,
      base_url: preset.base_url,
      api_key_env: preset.api_key_env,
      name: prev.name || preset.name,
    }));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const nextErrors = validateProviderForm(values, {
      editing: mode === "edit",
      existingIds: providers.map((candidate) => candidate.id),
    });
    setErrors(nextErrors);
    const first = firstFormError(nextErrors);
    if (first) {
      setNote({ kind: "error", text: first });
      return;
    }
    setSaving(true);
    setNote(null);
    try {
      if (mode === "edit" && provider) {
        const body = providerUpdateBody(provider, values);
        if (keyDraft.trim()) body.key = keyDraft.trim();
        const result = await updateProvider(provider.id, body);
        if (result.data) {
          await onSaved();
          onClose();
        } else {
          setNote({ kind: "error", text: apiErrorText(result) });
        }
      } else {
        const result = await createProvider({
          id: values.id.trim(),
          name: values.name.trim(),
          protocol: values.protocol,
          base_url: values.base_url.trim(),
          api_key_env: values.api_key_env.trim() || "LLM_API_KEY",
          key: keyDraft.trim(),
        });
        if (result.data) {
          await onSaved();
          onClose();
        } else {
          // Both protocols create now (#298 merged); protocol-specific refusals
          // (e.g. thinking / reasoning_effort on an Anthropic card) stay
          // server-enforced — show the service reason verbatim so the next step
          // is visible.
          setNote({ kind: "error", text: apiErrorText(result) });
        }
      }
    } catch {
      setNote({ kind: "error", text: "保存失败：无法连接后台；确认服务在运行后再试。" });
    } finally {
      setSaving(false);
    }
  };

  const clearKey = async () => {
    if (!provider) return;
    setClearArmed(false);
    setSaving(true);
    setNote(null);
    try {
      const result = await updateProvider(provider.id, { key: "" });
      if (result.data) {
        await onSaved();
        setNote({
          kind: "ok",
          text: result.data.key_set
            ? `已清除粘贴的 Key，回退到${KEY_SOURCE_LABEL[result.data.key_source] ?? "环境变量"}`
            : "已清除粘贴的 Key；当前未设置 Key",
        });
      } else {
        setNote({ kind: "error", text: apiErrorText(result) });
      }
    } catch {
      setNote({ kind: "error", text: "清除 Key 失败：无法连接后台；确认服务在运行后再试。" });
    } finally {
      setSaving(false);
    }
  };

  const protocolHint =
    PROTOCOL_OPTIONS.find((option) => option.value === values.protocol)?.hint ?? "";
  const presetFields = tab === "custom" || presetId !== "";
  // A preset tab without a selected preset has nothing to submit; keep the
  // button disabled and let the visible hint say which next step unblocks it.
  const presetMissing = mode === "create" && tab === "preset" && presetId === "";
  const errorFor = (field: keyof ProviderFormErrors) => errors[field] ?? "";

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="provider-dialog-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === dialogRef.current) dialogRef.current?.close();
      }}
      className="m-auto w-[min(680px,calc(100vw-2rem))] max-h-[min(85vh,calc(100dvh-2rem))] rounded-dialog border border-rule bg-surface-column p-0 text-primary backdrop:bg-surface-page/80"
    >
      <form onSubmit={submit} className="flex max-h-[inherit] flex-col">
        <header className="flex items-center gap-3 border-b border-rule px-5 py-4">
          <h2 id="provider-dialog-title" className="flex-1 text-title">
            {mode === "edit" ? "编辑 Provider" : "添加模型提供商"}
          </h2>
          <IconButton
            icon="close"
            aria-label="关闭 Provider 对话框"
            onClick={() => dialogRef.current?.close()}
          />
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-5 py-4">
          <p className="max-w-[52ch] text-body text-tertiary">
            一个 Provider = 一个 API 地址与协议，下挂多个模型；Key 只写不读。
            {mode === "create" && " Provider ID 创建后不可改。"}
          </p>

          {mode === "create" && (
            <>
              <Tabs
                label="添加方式"
                baseId="provider-dialog"
                value={tab}
                onChange={setTab}
                tabs={[
                  { id: "preset", label: "第三方模型供应商" },
                  { id: "custom", label: "自定义模型 API" },
                ]}
              />
              <div
                role="tabpanel"
                id={tabPanelId("provider-dialog", tab)}
                aria-labelledby={tabButtonId("provider-dialog", tab)}
                className="flex flex-col gap-3"
              >
                {tab === "preset" && (
                  <>
                    {presets.length === 0 ? (
                      <div className="flex flex-col gap-2 text-body text-tertiary" role="status">
                        <p>
                          后端当前没有可用的第三方供应商预设；用「自定义模型 API」填写地址与协议。
                        </p>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setTab("custom")}
                        >
                          改用自定义
                        </Button>
                      </div>
                    ) : (
                      <>
                        <div
                          className="flex flex-wrap gap-1.5"
                          role="group"
                          aria-label="第三方供应商预设"
                        >
                          {presets.map((preset) => (
                            <Button
                              key={preset.id}
                              size="sm"
                              variant={presetId === preset.id ? "outline" : "quiet"}
                              aria-pressed={presetId === preset.id}
                              onClick={() => applyPreset(preset)}
                            >
                              {preset.name || preset.id}
                            </Button>
                          ))}
                        </div>
                        {presetId === "" && (
                          <p className="text-meta text-tertiary">
                            选一个预设，地址与协议会自动填好；再填 ID、名称与 Key。
                          </p>
                        )}
                        {presetId !== "" && (
                          <p className="text-meta text-tertiary">
                            地址：
                            <span className="font-mono text-secondary">
                              {values.base_url}
                            </span>{" "}
                            · 协议：{protocolLabel(values.protocol)} · 环境变量：
                            <span className="font-mono">{values.api_key_env}</span>
                          </p>
                        )}
                      </>
                    )}
                  </>
                )}

                {tab === "custom" && (
                  <>
                    <FormField
                      label="API 地址"
                      name="base_url"
                      value={values.base_url}
                      onChange={(next) => setValues({ ...values, base_url: next })}
                      placeholder="https://…/v1"
                      error={errorFor("base_url")}
                    />
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-3">
                        <label
                          htmlFor="provider-protocol"
                          className="w-24 shrink-0 text-meta text-secondary"
                        >
                          API 协议
                        </label>
                        <select
                          id="provider-protocol"
                          value={values.protocol}
                          onChange={(event) => {
                            const next = event.target.value;
                            if (isModelProtocol(next)) {
                              setValues({ ...values, protocol: next });
                            }
                          }}
                          className="h-8 min-w-0 flex-1 rounded-control border border-rule bg-surface-well px-2 text-body text-primary"
                        >
                          {PROTOCOL_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </div>
                      <p className="pl-[6.75rem] text-meta text-tertiary">{protocolHint}</p>
                    </div>
                    <FormField
                      label="环境变量名"
                      name="api_key_env"
                      value={values.api_key_env}
                      onChange={(next) => setValues({ ...values, api_key_env: next })}
                      placeholder="LLM_API_KEY"
                      error={errorFor("api_key_env")}
                    />
                  </>
                )}

                {presetFields && (
                  <>
                    <FormField
                      label="Provider ID"
                      name="provider-id"
                      value={values.id}
                      onChange={(next) => setValues({ ...values, id: next })}
                      placeholder="例如 deepseek"
                      error={errorFor("id")}
                    />
                    <FormField
                      label="显示名称"
                      name="provider-name"
                      value={values.name}
                      onChange={(next) => setValues({ ...values, name: next })}
                      placeholder="例如：我的 DeepSeek"
                    />
                    <FormField
                      label="API 密钥"
                      name="provider-key"
                      type="password"
                      value={keyDraft}
                      onChange={setKeyDraft}
                      placeholder="留空则暂不设置，改用环境变量"
                    />
                  </>
                )}
              </div>
            </>
          )}

          {mode === "edit" && (
            <>
              <div className="flex items-center gap-3">
                <span className="w-24 shrink-0 text-meta text-secondary">Provider ID</span>
                <span className="min-w-0 flex-1 truncate font-mono text-machine text-secondary">
                  {provider?.id}
                </span>
                <span className="text-meta text-tertiary">创建后不可改</span>
              </div>
              <FormField
                label="显示名称"
                name="provider-name"
                value={values.name}
                onChange={(next) => setValues({ ...values, name: next })}
                placeholder="例如：我的 DeepSeek"
              />
              <FormField
                label="API 地址"
                name="base_url"
                value={values.base_url}
                onChange={(next) => setValues({ ...values, base_url: next })}
                placeholder="https://…/v1"
                error={errorFor("base_url")}
              />
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-3">
                  <label
                    htmlFor="provider-protocol-edit"
                    className="w-24 shrink-0 text-meta text-secondary"
                  >
                    API 协议
                  </label>
                  <select
                    id="provider-protocol-edit"
                    value={values.protocol}
                    onChange={(event) => {
                      const next = event.target.value;
                      if (isModelProtocol(next)) {
                        setValues({ ...values, protocol: next });
                      }
                    }}
                    className="h-8 min-w-0 flex-1 rounded-control border border-rule bg-surface-well px-2 text-body text-primary"
                  >
                    {PROTOCOL_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <p className="pl-[6.75rem] text-meta text-tertiary">{protocolHint}</p>
              </div>
              <FormField
                label="环境变量名"
                name="api_key_env"
                value={values.api_key_env}
                onChange={(next) => setValues({ ...values, api_key_env: next })}
                placeholder="LLM_API_KEY"
                error={errorFor("api_key_env")}
              />
              <FormField
                label="API 密钥"
                name="provider-key"
                type="password"
                value={keyDraft}
                onChange={setKeyDraft}
                placeholder="留空表示不修改（只写不读）"
              />
              {provider?.key_source === "pasted" && (
                <div className="flex flex-wrap items-center gap-2 pl-[6.75rem]">
                  {clearArmed ? (
                    <>
                      <Button size="sm" variant="danger" onClick={clearKey} disabled={saving}>
                        确认清除
                      </Button>
                      <Button size="sm" onClick={() => setClearArmed(false)}>
                        取消
                      </Button>
                    </>
                  ) : (
                    <Button size="sm" onClick={() => setClearArmed(true)}>
                      清除已保存的 Key
                    </Button>
                  )}
                  <span className="text-meta text-tertiary">清除后回退到环境变量</span>
                </div>
              )}
            </>
          )}
        </div>

        <footer className="flex flex-wrap items-center gap-2 border-t border-rule px-5 py-3">
          <Button
            type="submit"
            variant="outline"
            size="sm"
            disabled={saving || presetMissing}
            title={presetMissing ? "先选择预设或改用自定义模型 API" : undefined}
          >
            {saving ? "保存中…" : mode === "edit" ? "保存" : "创建"}
          </Button>
          <Button size="sm" onClick={() => dialogRef.current?.close()} disabled={saving}>
            取消
          </Button>
          {presetMissing && !note && (
            <span className="min-w-0 flex-1 text-meta text-tertiary">
              先选择上面的预设，或改用「自定义模型 API」。
            </span>
          )}
          {note && (
            <span
              role={note.kind === "error" ? "alert" : "status"}
              className={
                note.kind === "error"
                  ? "min-w-0 flex-1 text-meta text-state-danger"
                  : "min-w-0 flex-1 text-meta text-secondary"
              }
            >
              {note.text}
            </span>
          )}
        </footer>
      </form>
    </dialog>
  );
}
