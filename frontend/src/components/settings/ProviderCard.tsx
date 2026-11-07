import { useState } from "react";
import {
  addProviderModel,
  apiErrorText,
  deleteProvider,
  deleteProviderModel,
  discoverProviderModels,
  setDefaultModel,
  testModel,
} from "../../api/client";
import { BACKEND_UNREACHABLE } from "../../lib/copy";
import { cx } from "../../lib/cx";
import {
  discoveryBlockedReason,
  discoveryFailureNote,
  initialDiscoverySelection,
  keyStateText,
  manualModelError,
  pickableCandidates,
  protocolLabel,
  providerDefaultAction,
  toggleDiscoveryPick,
} from "../../lib/modelCatalog";
import type { DiscoverCandidate, ModelEntry, Provider } from "../../types";
import { Button } from "../ui/Button";

interface ProviderCardProps {
  provider: Provider;
  models: ModelEntry[];
  /** Global default model entry id (the backend default is entry-level). */
  defaultId: string;
  onEdit: () => void;
  /** Reload the shared provider + catalog store after a mutation. */
  onRefresh: () => Promise<void>;
}

type Note = { kind: "ok" | "error"; text: string } | null;

/**
 * One Provider card (#243 P2-3 / D-37): the card facts, the mutation actions,
 * the model catalog and the discovery preview. Keys are write-only, so the card
 * shows key_set / key_source only.
 */
export function ProviderCard({
  provider,
  models,
  defaultId,
  onEdit,
  onRefresh,
}: ProviderCardProps) {
  const [armed, setArmed] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addModel, setAddModel] = useState("");
  const [addName, setAddName] = useState("");
  const [addError, setAddError] = useState("");
  const [addingModel, setAddingModel] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState("");
  const [candidates, setCandidates] = useState<DiscoverCandidate[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [addingPicked, setAddingPicked] = useState(false);

  const defaultAction = providerDefaultAction(models, defaultId);
  const discoverBlocked = discoveryBlockedReason(provider.protocol);
  const pickable = candidates ? pickableCandidates(candidates, picked) : [];

  const report = (text: string) => setNote({ kind: "ok", text });
  const reportError = (text: string) => setNote({ kind: "error", text });

  const makeDefault = async () => {
    if (!defaultAction.entryId) return;
    try {
      const result = await setDefaultModel(defaultAction.entryId);
      if (result.data) {
        report("已设为默认");
        await onRefresh();
      } else {
        reportError(apiErrorText(result));
      }
    } catch {
      reportError("设置默认失败：" + BACKEND_UNREACHABLE);
    }
  };

  const removeProvider = async () => {
    setArmed(false);
    try {
      const result = await deleteProvider(provider.id);
      if (result.data) {
        await onRefresh();
      } else {
        reportError(
          `${apiErrorText(result)}；先给其它 Provider 添加模型，再删这一个。`
        );
      }
    } catch {
      reportError("删除 Provider 失败：" + BACKEND_UNREACHABLE);
    }
  };

  const removeModel = async (modelId: string) => {
    try {
      const result = await deleteProviderModel(provider.id, modelId);
      if (result.data) {
        report("已删除模型");
        await onRefresh();
      } else {
        reportError(`${apiErrorText(result)}；目录至少要保留一个模型。`);
      }
    } catch {
      reportError("删除模型失败：" + BACKEND_UNREACHABLE);
    }
  };

  const test = async (entryId: string) => {
    report("测试中…");
    try {
      const result = await testModel(entryId);
      if (result.data?.ok) report(result.data.note ?? "连接正常");
      else reportError(result.data?.error ?? apiErrorText(result));
    } catch {
      reportError("测试失败：" + BACKEND_UNREACHABLE);
    }
  };

  const submitModel = async () => {
    const error = manualModelError(addModel, models);
    setAddError(error);
    if (error) return;
    setAddingModel(true);
    try {
      const result = await addProviderModel(provider.id, {
        model: addModel.trim(),
        name: addName.trim() || undefined,
      });
      if (result.data) {
        setAddOpen(false);
        setAddModel("");
        setAddName("");
        setAddError("");
        report(`已添加模型 ${result.data.model}`);
        await onRefresh();
      } else {
        setAddError(apiErrorText(result));
      }
    } catch {
      setAddError("添加失败：" + BACKEND_UNREACHABLE);
    } finally {
      setAddingModel(false);
    }
  };

  const discover = async () => {
    setDiscovering(true);
    setDiscoverError("");
    setCandidates(null);
    setPicked([]);
    try {
      const result = await discoverProviderModels(provider.id);
      if (result.data) {
        const list = result.data.models ?? [];
        setCandidates(list);
        setPicked(initialDiscoverySelection(list));
        if (list.length === 0) {
          report("上游没有返回模型；可以手动添加模型 ID。");
          setCandidates(null);
        }
      } else {
        setDiscoverError(discoveryFailureNote(apiErrorText(result)));
      }
    } catch {
      setDiscoverError(discoveryFailureNote(""));
    } finally {
      setDiscovering(false);
    }
  };

  const addPicked = async () => {
    const chosen = pickableCandidates(candidates ?? [], picked);
    if (chosen.length === 0) return;
    setAddingPicked(true);
    const added = new Set<string>();
    const failed: string[] = [];
    for (const candidate of chosen) {
      try {
        const result = await addProviderModel(provider.id, {
          model: candidate.model,
          name: candidate.name || undefined,
        });
        if (result.data) added.add(candidate.model);
        else failed.push(`${candidate.model}（${apiErrorText(result)}）`);
      } catch {
        failed.push(`${candidate.model}（${BACKEND_UNREACHABLE}）`);
      }
    }
    setAddingPicked(false);
    setPicked([]);
    if (added.size > 0) {
      setCandidates((prev) =>
        prev
          ? prev.map((candidate) =>
              added.has(candidate.model) ? { ...candidate, added: true } : candidate
            )
          : prev
      );
      await onRefresh();
    }
    if (failed.length > 0) {
      reportError(
        `加入目录：成功 ${added.size} 个，失败 ${failed.length} 个（${failed.join("；")}）`
      );
    } else {
      report(`已加入目录 ${added.size} 个模型`);
    }
  };

  return (
    <section
      data-provider-id={provider.id}
      aria-labelledby={`provider-title-${provider.id}`}
      className="flex flex-col gap-2 rounded-well border border-rule bg-surface-sheet p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3
          id={`provider-title-${provider.id}`}
          className="min-w-0 truncate text-title text-primary"
          title={provider.name || provider.id}
        >
          {provider.name || provider.id}
        </h3>
        {provider.preset && (
          <span className="rounded-full border border-rule px-1.5 py-0.5 text-machine text-tertiary">
            预设
          </span>
        )}
        {defaultAction.isDefault && (
          <span className="rounded-full border border-rule-strong px-1.5 py-0.5 text-machine text-primary">
            默认
          </span>
        )}
        <span
          className="min-w-0 flex-1 truncate font-mono text-machine text-tertiary"
          title={provider.base_url}
        >
          {provider.base_url}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-tertiary">
        <span>协议：{protocolLabel(provider.protocol)}</span>
        <span>
          环境变量：<span className="font-mono">{provider.api_key_env}</span>
        </span>
        <span>Key：{keyStateText(provider)}</span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          size="sm"
          onClick={makeDefault}
          disabled={!defaultAction.entryId}
          title={defaultAction.disabledReason || undefined}
          aria-label={`设为默认：${provider.name || provider.id}`}
        >
          设为默认
        </Button>
        <Button
          size="sm"
          onClick={onEdit}
          aria-label={`编辑 Provider：${provider.name || provider.id}`}
        >
          编辑
        </Button>
        {armed ? (
          <>
            <Button
              size="sm"
              variant="danger"
              onClick={removeProvider}
              aria-label={`确认删除 Provider：${provider.name || provider.id}`}
            >
              确认删除
            </Button>
            <Button size="sm" onClick={() => setArmed(false)}>
              取消
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="danger"
            onClick={() => setArmed(true)}
            aria-label={`删除 Provider：${provider.name || provider.id}`}
          >
            删除
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-2 rounded-well border border-rule bg-surface-well p-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-meta font-semibold text-secondary">模型目录</span>
          <span className="text-machine text-tertiary">{models.length} 个</span>
        </div>

        {models.length === 0 ? (
          <p className="text-meta text-tertiary">
            暂无模型；用「获取可用模型」勾选，或手动添加模型 ID。
          </p>
        ) : (
          <ul className="flex flex-col">
            {models.map((entry) => (
              <li
                key={entry.id}
                data-model-id={entry.id}
                className="flex flex-wrap items-center gap-2 border-t border-rule py-1.5 text-meta first:border-t-0 first:pt-0"
              >
                <span
                  className="min-w-0 truncate font-mono text-primary"
                  title={entry.model}
                >
                  {entry.model}
                </span>
                {entry.name !== entry.model && (
                  <span
                    className="min-w-0 truncate text-secondary"
                    title={entry.name}
                  >
                    {entry.name}
                  </span>
                )}
                {entry.id === defaultId && (
                  <span className="rounded-full border border-rule-strong px-1.5 py-0.5 text-machine text-primary">
                    默认
                  </span>
                )}
                <span className="ml-auto flex items-center gap-1">
                  <Button
                    size="sm"
                    onClick={() => test(entry.id)}
                    aria-label={`测试模型：${entry.model}`}
                  >
                    测试
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => removeModel(entry.id)}
                    aria-label={`删除模型：${entry.model}`}
                  >
                    删除
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={discover}
            disabled={discovering || discoverBlocked !== ""}
            title={discoverBlocked || undefined}
          >
            {discovering ? "获取中…" : "获取可用模型"}
          </Button>
          <Button size="sm" onClick={() => setAddOpen((prev) => !prev)}>
            手动添加模型
          </Button>
        </div>
        {discoverBlocked && (
          <p className="text-meta text-tertiary">{discoverBlocked}</p>
        )}

        {addOpen && (
          <div className="flex flex-col gap-2 border-t border-rule pt-2">
            <div className="flex flex-wrap items-center gap-2">
              <input
                aria-label={`目录外的模型 ID：${provider.name || provider.id}`}
                value={addModel}
                placeholder="模型 ID，例如 qwen-max"
                onChange={(event) => setAddModel(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void submitModel();
                  }
                }}
                className="h-7 min-w-0 flex-1 rounded-control border border-rule bg-surface-sheet px-2 font-mono text-machine text-primary placeholder:text-tertiary"
              />
              <input
                aria-label={`模型显示名称（可选）：${provider.name || provider.id}`}
                value={addName}
                placeholder="显示名称（可选）"
                onChange={(event) => setAddName(event.target.value)}
                className="h-7 w-40 rounded-control border border-rule bg-surface-sheet px-2 text-machine text-primary placeholder:text-tertiary"
              />
              <Button size="sm" onClick={submitModel} disabled={addingModel}>
                {addingModel ? "添加中…" : "添加"}
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  setAddOpen(false);
                  setAddError("");
                }}
              >
                取消
              </Button>
            </div>
            {addError && (
              <div role="alert" className="text-meta text-state-danger">
                {addError}
              </div>
            )}
          </div>
        )}

        {discovering && (
          <div role="status" className="text-meta text-tertiary">
            正在获取可用模型…
          </div>
        )}
        {discoverError && (
          <div role="alert" className="flex flex-wrap items-center gap-2 text-meta text-state-danger">
            <span className="min-w-0 flex-1">{discoverError}</span>
            <Button size="sm" onClick={discover} disabled={discovering}>
              重试
            </Button>
          </div>
        )}
        {candidates && candidates.length > 0 && (
          <div className="flex flex-col gap-2 border-t border-rule pt-2">
            <div className="text-meta text-secondary">
              预览（未保存）：勾选要加入目录的模型
            </div>
            <ul className="flex flex-col gap-1">
              {candidates.map((candidate) => (
                <li key={candidate.model} className="flex items-center gap-2 text-meta">
                  <input
                    type="checkbox"
                    aria-label={`加入 ${candidate.model}`}
                    checked={candidate.added || picked.includes(candidate.model)}
                    disabled={candidate.added || addingPicked}
                    onChange={() => setPicked((prev) => toggleDiscoveryPick(prev, candidate.model))}
                    className="accent-signal-now"
                  />
                  <span className="min-w-0 truncate font-mono text-primary">
                    {candidate.model}
                  </span>
                  {candidate.name !== candidate.model && (
                    <span className="min-w-0 truncate text-tertiary">
                      {candidate.name}
                    </span>
                  )}
                  {candidate.added && <span className="text-tertiary">已在目录</span>}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={addPicked}
                disabled={addingPicked || pickable.length === 0}
              >
                {addingPicked ? "加入中…" : `加入目录（${pickable.length}）`}
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  setCandidates(null);
                  setPicked([]);
                }}
              >
                收起预览
              </Button>
            </div>
          </div>
        )}
      </div>

      {note && (
        <div
          role={note.kind === "error" ? "alert" : "status"}
          className={cx(
            "text-meta",
            note.kind === "error" ? "text-state-danger" : "text-secondary"
          )}
        >
          {note.text}
        </div>
      )}
    </section>
  );
}
