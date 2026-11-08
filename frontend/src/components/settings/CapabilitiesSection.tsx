import { useEffect, useId, useRef, useState } from "react";
import {
  activeFaceSummary,
  buildPluginsPayload,
  changePreset,
  effectivePackageEnabled,
  packageBadges,
  pluginsDirty,
  pluginsDraftFrom,
  presetLabel,
  presetPackages,
  restartNotice,
  setOverride,
  shouldAdoptSnapshot,
  type OverrideState,
  type PluginsDraft,
} from "../../lib/plugins";
import { usePluginsStore } from "../../store/pluginsStore";
import type { PluginConfigured, PluginsData } from "../../types";
import { Button } from "../ui/Button";
import { Segmented } from "../ui/Segmented";

const OVERRIDE_OPTIONS: ReadonlyArray<{ value: OverrideState; label: string }> = [
  { value: "follow", label: "跟随预设" },
  { value: "on", label: "强制开" },
  { value: "off", label: "强制关" },
];

function isOverrideState(value: string): value is OverrideState {
  return value === "follow" || value === "on" || value === "off";
}

/**
 * #67 P4-2 / ADR 0012: settings "能力" section.
 *
 * Three fixed presets + per-package tri-state overrides (follow / force on /
 * force off); saving writes `data/plugins.json` and a backend restart is what
 * changes the running face. The active face shown here comes straight from
 * `GET /plugins` — this component never re-derives preset ∩ overrides.
 */
export function CapabilitiesSection() {
  const data = usePluginsStore((s) => s.data);
  const loading = usePluginsStore((s) => s.loading);
  const error = usePluginsStore((s) => s.error);
  const saving = usePluginsStore((s) => s.saving);
  const saveError = usePluginsStore((s) => s.saveError);
  const saveNote = usePluginsStore((s) => s.saveNote);
  const refresh = usePluginsStore((s) => s.refresh);
  const save = usePluginsStore((s) => s.save);
  const clearSaveNotes = usePluginsStore((s) => s.clearSaveNotes);

  const [draft, setDraft] = useState<PluginsDraft | null>(null);
  const baseId = useId();
  // #308 M2: the saved config the draft was seeded from (dirty detection) and
  // the latest draft for the snapshot-adoption effect.
  const baseRef = useRef<PluginConfigured | null>(null);
  const draftRef = useRef<PluginsDraft | null>(null);

  // The section mounts on every settings open (the dialog unmounts its content
  // while closed), so a plain mount-time GET is the fresh-on-reopen rule.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  const adopt = (snapshot: PluginsData) => {
    baseRef.current = snapshot.saved;
    setDraft(pluginsDraftFrom(snapshot.saved, snapshot.packages));
  };

  // A late or refreshed snapshot may only replace a clean draft: while the user
  // has unsaved edits, adopting would silently throw those away. The active
  // face / restart hint still re-render from the store data either way.
  useEffect(() => {
    if (!data) return;
    if (!shouldAdoptSnapshot(baseRef.current, draftRef.current)) return;
    adopt(data);
    // adopt is render-scoped; data is the only trigger that matters here.
  }, [data]);

  const edit = () => clearSaveNotes();
  const dirty = data && draft ? pluginsDirty(data.saved, draft) : false;

  const choosePreset = (preset: string) => {
    if (saving) return;
    edit();
    setDraft((current) => (current ? changePreset(current, preset) : current));
  };

  const chooseOverride = (packageId: string, state: OverrideState) => {
    if (saving) return;
    edit();
    setDraft((current) => (current ? setOverride(current, packageId, state) : current));
  };

  const saveDraft = async () => {
    if (!draft || saving) return;
    const ok = await save(buildPluginsPayload(draft));
    if (!ok) return;
    // The PUT response is authoritative: re-seed the draft from it so the
    // save button's dirty check is measured against the new saved config.
    const fresh = usePluginsStore.getState().data;
    if (fresh) adopt(fresh);
  };

  return (
    <fieldset
      aria-labelledby={`${baseId}-title`}
      className="flex flex-col gap-3 rounded-well border border-rule bg-surface-sheet p-3"
    >
      <legend id={`${baseId}-title`} className="px-1 text-title text-primary">
        能力
      </legend>
      <p className="max-w-[56ch] text-meta text-tertiary">
        能力包决定后台挂载哪些工具；预设是一套固定组合，单包覆盖（强制开 / 关）叠在预设之上。
        保存写入 data/plugins.json，重启后台后生效。
      </p>

      {loading && !data && (
        <div role="status" className="text-body text-tertiary">
          加载能力配置…
        </div>
      )}
      {error && (
        <div className="flex flex-wrap items-center gap-2 text-meta text-state-danger">
          <span role="alert" className="min-w-0 flex-1">
            {error}
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void refresh()}
            disabled={loading}
          >
            {loading ? "重试中…" : "重试"}
          </Button>
        </div>
      )}

      {data && draft && (
        <>
          <div className="flex flex-col gap-1.5">
            <span className="text-meta font-semibold text-secondary">预设</span>
            <Segmented
              options={data.presets.map((preset) => ({
                value: preset.id,
                label: presetLabel(preset.id),
                title: `${preset.id}：${preset.packages.join("、") || "无能力包"}`,
              }))}
              value={draft.preset}
              onChange={choosePreset}
              label="能力预设"
              disabled={saving}
            />
            <span className="text-machine text-tertiary">
              预设包含：
              {presetPackages(data.presets, draft.preset).join("、") || "无能力包"}
            </span>
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-meta font-semibold text-secondary">
              能力包覆盖（{data.packages.length} 个）
            </span>
            <ul className="flex flex-col">
              {data.packages.map((pkg) => {
                const selectId = `${baseId}-pkg-${pkg.id}`;
                const active = effectivePackageEnabled(data.active.packages, pkg.id);
                return (
                  <li
                    key={pkg.id}
                    className="flex flex-wrap items-center gap-2 border-t border-rule py-1.5 text-meta first:border-t-0 first:pt-0"
                  >
                    <label
                      htmlFor={selectId}
                      className="min-w-0 shrink-0 truncate font-mono text-primary"
                      title={pkg.id}
                    >
                      {pkg.id}
                    </label>
                    <span className="flex flex-wrap items-center gap-1 text-machine text-tertiary">
                      {packageBadges(pkg).map((badge) => (
                        <span
                          key={badge}
                          className="rounded-full border border-rule px-1.5 py-0.5"
                        >
                          {badge}
                        </span>
                      ))}
                    </span>
                    {active && (
                      <span className="rounded-full border border-rule-strong px-1.5 py-0.5 text-machine text-primary">
                        当前生效
                      </span>
                    )}
                    <select
                      id={selectId}
                      value={draft.states[pkg.id] ?? "follow"}
                      disabled={saving}
                      onChange={(event) => {
                        if (isOverrideState(event.target.value)) {
                          chooseOverride(pkg.id, event.target.value);
                        }
                      }}
                      className="ml-auto h-7 rounded-control border border-rule bg-surface-well px-2 text-meta text-primary disabled:opacity-50"
                    >
                      {OVERRIDE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="flex flex-col gap-1 rounded-well bg-surface-well p-2">
            <span className="text-meta font-semibold text-secondary">
              当前生效（后台已加载）
            </span>
            <span className="font-mono text-machine tabular-nums text-tertiary">
              {activeFaceSummary(data.active).summary}
            </span>
            <span className="text-machine text-tertiary">
              能力包：
              {data.active.packages.length
                ? data.active.packages.join("、")
                : "无"}
            </span>
            <div className="flex flex-wrap gap-1">
              {data.active.tools.length === 0 ? (
                <span className="text-machine text-tertiary">无工具</span>
              ) : (
                data.active.tools.map((tool) => (
                  <span
                    key={tool}
                    className="max-w-full break-all rounded-control bg-surface-sheet px-1.5 py-0.5 font-mono text-machine text-secondary"
                    title={tool}
                  >
                    {tool}
                  </span>
                ))
              )}
            </div>
            <span
              role="status"
              className={
                data.restart_required
                  ? "text-meta text-state-warn"
                  : "text-meta text-tertiary"
              }
            >
              {restartNotice(data.restart_required)}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={saveDraft}
              disabled={saving || !dirty}
            >
              {saving ? "保存中…" : "保存能力配置"}
            </Button>
            {data.packages.length === 0 && (
              <span className="text-meta text-tertiary">没有可配置的能力包。</span>
            )}
            {saveError && (
              <span role="alert" className="min-w-0 flex-1 text-meta text-state-danger">
                {saveError}
              </span>
            )}
            {saveNote && !saveError && (
              <span role="status" className="min-w-0 flex-1 text-meta text-secondary">
                {saveNote}
              </span>
            )}
          </div>
        </>
      )}
    </fieldset>
  );
}
