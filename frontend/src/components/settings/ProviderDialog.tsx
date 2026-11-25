import { FormEvent, useEffect, useRef, useState } from "react";
import {
  apiErrorText,
  createProvider,
  discoverDraftProviderModels,
  updateProvider,
} from "../../api/client";
import {
  bulkDraftSelection,
  discoveryBlockedReason,
  discoveryFailureNote,
  draftProviderModels,
  filterDraftCandidates,
  firstFormError,
  isModelProtocol,
  KEY_SOURCE_LABEL,
  manualDraftModelError,
  OPENCODE_GO_TEMPLATE,
  PROTOCOL_OPTIONS,
  protocolLabel,
  providerUpdateBody,
  templateAvailable,
  toggleDiscoveryPick,
  validateProviderForm,
  type ProviderFormErrors,
  type ProviderFormValues,
} from "../../lib/modelCatalog";
import type {
  DraftModelCandidate,
  Provider,
  ProviderCreateRequest,
  ProviderModelDraft,
} from "../../types";
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
 * Create offers the two tabs from the user story: a built-in OpenCode Go
 * template, preset-backed third-party suppliers (only when the backend actually
 * reports `preset` cards — the service has no separate preset catalog, so the
 * page never invents one) and a fully custom API. The template fills the custom
 * form and hands the user there, so validation and draft discovery stay single.
 * Edit keeps the id immutable and never re-sends an unchanged protocol: an
 * existing anthropic card must stay editable for its other fields, and
 * re-sending the protocol would re-run the protocol-specific parameter checks
 * (Anthropic rejects thinking / reasoning_effort at the write face).
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
  /** Draft discovery results; null = never fetched. Picks only hold candidate ids. */
  const [draftCandidates, setDraftCandidates] = useState<DraftModelCandidate[] | null>(
    null
  );
  const [draftPicked, setDraftPicked] = useState<string[]>([]);
  const [draftSearch, setDraftSearch] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState("");
  /** Manual rows survive a discovery failure and are saved with the provider. */
  const [manualModels, setManualModels] = useState<ProviderModelDraft[]>([]);
  const [manualModel, setManualModel] = useState("");
  const [manualName, setManualName] = useState("");
  const [manualError, setManualError] = useState("");
  /** Drops a discovery answer that belongs to a superseded form config. */
  const discoverySeq = useRef(0);

  /** Drop the preview and invalidate an in-flight answer; manual rows stay. */
  const resetDraftPreview = () => {
    discoverySeq.current += 1;
    setDraftCandidates(null);
    setDraftPicked([]);
    setDraftSearch("");
    setDiscovering(false);
    setDiscoverError("");
  };

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
    resetDraftPreview();
    setManualModels([]);
    setManualModel("");
    setManualName("");
    setManualError("");
  }, [open, provider?.id]);

  // #353: candidates belong to the address / protocol / env / key that produced
  // them. A later edit makes a stale pick unsafe, so drop the whole preview
  // (manual rows stay — they are not tied to the probe). The generation bump
  // also discards an in-flight answer.
  useEffect(() => {
    resetDraftPreview();
  }, [open, values.protocol, values.base_url, values.api_key_env, keyDraft]);

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

  /** #353: the built-in template fills the same form the custom tab uses and
   * hands the user there — the discovery / validation path stays single. */
  const applyOpencodeGoTemplate = () => {
    setPresetId("");
    setErrors({});
    setValues({ ...OPENCODE_GO_TEMPLATE });
    setTab("custom");
  };

  const discoverDraft = async () => {
    const seq = ++discoverySeq.current;
    setDiscovering(true);
    setDiscoverError("");
    setDraftCandidates(null);
    setDraftPicked([]);
    setDraftSearch("");
    try {
      const result = await discoverDraftProviderModels({
        protocol: values.protocol,
        base_url: values.base_url.trim(),
        api_key_env: values.api_key_env.trim() || "LLM_API_KEY",
        key: keyDraft.trim(),
      });
      if (seq !== discoverySeq.current) return; // form changed mid-flight
      if (result.data) {
        const list = result.data.models ?? [];
        setDraftCandidates(list);
        setDraftPicked(bulkDraftSelection(list, "all"));
      } else {
        setDiscoverError(discoveryFailureNote(apiErrorText(result)));
      }
    } catch {
      if (seq === discoverySeq.current) {
        setDiscoverError(discoveryFailureNote(""));
      }
    } finally {
      if (seq === discoverySeq.current) setDiscovering(false);
    }
  };

  const addManualModel = () => {
    const error = manualDraftModelError(
      manualModel,
      draftCandidates ?? [],
      manualModels
    );
    setManualError(error);
    if (error) return;
    setManualModels((prev) => [
      ...prev,
      { model: manualModel.trim(), name: manualName.trim() },
    ]);
    setManualModel("");
    setManualName("");
  };

  const removeManualModel = (model: string) => {
    setManualModels((prev) => prev.filter((row) => row.model !== model));
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
        // #353: only this explicit save writes; the discovery probe itself is
        // read-only. Selected candidates + manual rows travel together so the
        // provider and its catalog appear atomically.
        const models = draftProviderModels(
          draftCandidates ?? [],
          draftPicked,
          manualModels
        );
        const body: ProviderCreateRequest = {
          id: values.id.trim(),
          name: values.name.trim(),
          protocol: values.protocol,
          base_url: values.base_url.trim(),
          api_key_env: values.api_key_env.trim() || "LLM_API_KEY",
          key: keyDraft.trim(),
        };
        if (models.length > 0) body.models = models;
        const result = await createProvider(body);
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
  const opencodeGoAvailable = templateAvailable(OPENCODE_GO_TEMPLATE.id, providers);
  const discoverBlocked = discoveryBlockedReason(values.protocol);
  const draftDiscoverDisabled =
    saving || discovering || discoverBlocked !== "" || !values.base_url.trim();
  const draftDiscoverReason =
    discoverBlocked ||
    (!values.base_url.trim() ? "先填 API 地址，再获取可用模型。" : "");
  const draftFiltered = draftCandidates
    ? filterDraftCandidates(draftCandidates, draftSearch)
    : [];

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
                    {opencodeGoAvailable ? (
                      <div className="flex flex-col gap-1.5">
                        <div
                          className="flex flex-wrap gap-1.5"
                          role="group"
                          aria-label="内置预设"
                        >
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={applyOpencodeGoTemplate}
                          >
                            {OPENCODE_GO_TEMPLATE.name}
                          </Button>
                        </div>
                        <p className="text-meta text-tertiary">
                          官方地址：
                          <span className="font-mono text-secondary">
                            {OPENCODE_GO_TEMPLATE.base_url}
                          </span>{" "}
                          · 协议：{protocolLabel(OPENCODE_GO_TEMPLATE.protocol)} ·
                          环境变量：
                          <span className="font-mono">
                            {OPENCODE_GO_TEMPLATE.api_key_env}
                          </span>
                          ；选中后切到自定义表单，地址、协议、环境变量名和 Provider ID
                          会填好，仍可修改。
                        </p>
                      </div>
                    ) : (
                      <p className="text-meta text-tertiary">
                        OpenCode Go 已在 Provider 列表中（ID：
                        <span className="font-mono">{OPENCODE_GO_TEMPLATE.id}</span>
                        ），不能重复添加。
                      </p>
                    )}
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
                    {mode === "create" && tab === "custom" && (
                      <div className="flex flex-col gap-2 rounded-well border border-rule bg-surface-well p-3">
                        <div className="flex flex-wrap items-baseline gap-2">
                          <span className="text-meta font-semibold text-secondary">
                            模型目录（可选）
                          </span>
                          <span className="text-meta text-tertiary">
                            保存时随 Provider 一起创建；关闭或切换页签不写入。
                          </span>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={discoverDraft}
                            disabled={draftDiscoverDisabled}
                            title={draftDiscoverReason || undefined}
                          >
                            {discovering ? "获取中…" : "获取可用模型"}
                          </Button>
                          <span className="min-w-0 flex-1 text-meta text-tertiary">
                            用当前地址、协议、环境变量和 Key 探测；Key
                            留空时读环境变量。
                          </span>
                        </div>
                        {draftDiscoverReason && (
                          <p className="text-meta text-tertiary">{draftDiscoverReason}</p>
                        )}

                        {draftCandidates !== null && draftCandidates.length === 0 && (
                          <p className="text-meta text-tertiary">
                            上游没有返回模型；可以手动添加模型 ID。
                          </p>
                        )}

                        {draftCandidates !== null && draftCandidates.length > 0 && (
                          <div className="flex flex-col gap-2 border-t border-rule pt-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <input
                                aria-label="搜索候选模型"
                                name="draft-model-search"
                                autoComplete="off"
                                spellCheck={false}
                                value={draftSearch}
                                placeholder="搜索模型 ID 或名称"
                                onChange={(event) => setDraftSearch(event.target.value)}
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") event.preventDefault();
                                }}
                                className="h-7 min-w-0 flex-1 rounded-control border border-rule bg-surface-sheet px-2 font-mono text-machine text-primary placeholder:text-tertiary"
                              />
                              <Button
                                size="sm"
                                onClick={() =>
                                  setDraftPicked(
                                    bulkDraftSelection(draftCandidates, "all")
                                  )
                                }
                              >
                                全选
                              </Button>
                              <Button
                                size="sm"
                                onClick={() =>
                                  setDraftPicked(
                                    bulkDraftSelection(draftCandidates, "none")
                                  )
                                }
                                disabled={draftPicked.length === 0}
                              >
                                取消全选
                              </Button>
                              <span className="text-machine text-tertiary">
                                已选 {draftPicked.length} / {draftCandidates.length}
                              </span>
                            </div>
                            {draftFiltered.length === 0 ? (
                              <p className="text-meta text-tertiary">
                                没有匹配的候选模型。
                              </p>
                            ) : (
                              <ul className="flex flex-col gap-1">
                                {draftFiltered.map((candidate) => (
                                  <li
                                    key={candidate.model}
                                    className="flex items-center gap-2 text-meta"
                                  >
                                    <input
                                      type="checkbox"
                                      aria-label={`选择模型 ${candidate.model}`}
                                      checked={draftPicked.includes(candidate.model)}
                                      onChange={() =>
                                        setDraftPicked((prev) =>
                                          toggleDiscoveryPick(prev, candidate.model)
                                        )
                                      }
                                      className="accent-signal-now"
                                    />
                                    <span
                                      className="min-w-0 truncate font-mono text-primary"
                                      title={candidate.model}
                                    >
                                      {candidate.model}
                                    </span>
                                    {candidate.name !== candidate.model && (
                                      <span
                                        className="min-w-0 truncate text-tertiary"
                                        title={candidate.name}
                                      >
                                        {candidate.name}
                                      </span>
                                    )}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}

                        {discovering && (
                          <div role="status" className="text-meta text-tertiary">
                            正在获取可用模型…
                          </div>
                        )}
                        {discoverError && (
                          <div
                            role="alert"
                            className="flex flex-wrap items-center gap-2 text-meta text-state-danger"
                          >
                            <span className="min-w-0 flex-1">{discoverError}</span>
                            <Button
                              size="sm"
                              onClick={discoverDraft}
                              disabled={discovering}
                            >
                              重试
                            </Button>
                          </div>
                        )}

                        <div className="flex flex-col gap-2 border-t border-rule pt-2">
                          <span className="text-meta text-secondary">手动添加模型</span>
                          <div className="flex flex-wrap items-center gap-2">
                            <input
                              aria-label="手动模型 ID"
                              name="manual-model-id"
                              autoComplete="off"
                              spellCheck={false}
                              value={manualModel}
                              placeholder="模型 ID，例如 claude-sonnet-4"
                              onChange={(event) => setManualModel(event.target.value)}
                              onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                  event.preventDefault();
                                  addManualModel();
                                }
                              }}
                              className="h-7 min-w-0 flex-1 rounded-control border border-rule bg-surface-sheet px-2 font-mono text-machine text-primary placeholder:text-tertiary"
                            />
                            <input
                              aria-label="模型显示名称（可选）"
                              name="manual-model-name"
                              autoComplete="off"
                              value={manualName}
                              placeholder="显示名称（可选）"
                              onChange={(event) => setManualName(event.target.value)}
                              onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                  event.preventDefault();
                                  addManualModel();
                                }
                              }}
                              className="h-7 w-40 rounded-control border border-rule bg-surface-sheet px-2 font-mono text-machine text-primary placeholder:text-tertiary"
                            />
                            <Button size="sm" onClick={addManualModel}>
                              添加
                            </Button>
                          </div>
                          {manualError && (
                            <p role="alert" className="text-meta text-state-danger">
                              {manualError}
                            </p>
                          )}
                          {manualModels.length > 0 && (
                            <ul className="flex flex-col">
                              {manualModels.map((row) => (
                                <li
                                  key={row.model}
                                  className="flex flex-wrap items-center gap-2 border-t border-rule py-1.5 text-meta first:border-t-0 first:pt-0"
                                >
                                  <span
                                    className="min-w-0 truncate font-mono text-primary"
                                    title={row.model}
                                  >
                                    {row.model}
                                  </span>
                                  {row.name && row.name !== row.model && (
                                    <span
                                      className="min-w-0 truncate text-tertiary"
                                      title={row.name}
                                    >
                                      {row.name}
                                    </span>
                                  )}
                                  <Button
                                    size="sm"
                                    variant="danger"
                                    className="ml-auto"
                                    onClick={() => removeManualModel(row.model)}
                                    aria-label={`移除手动模型 ${row.model}`}
                                  >
                                    移除
                                  </Button>
                                </li>
                              ))}
                            </ul>
                          )}
                          <p className="text-meta text-tertiary">
                            发现失败时也可以在这里填模型 ID，保存时一起创建。
                          </p>
                        </div>
                      </div>
                    )}
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
