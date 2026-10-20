import { ModelProfile } from "../types";

/**
 * Compact per-task model picker (M2), styled like {@link ModeSwitch}.
 *
 * An existing task applies the change on the next message; a draft task keeps
 * the choice until it is created.
 */
export function ModelSelect({
  value,
  models,
  disabled,
  onChange,
}: {
  value: string;
  models: ModelProfile[];
  disabled?: boolean;
  onChange: (modelId: string) => void;
}) {
  return (
    <label className="inline-flex items-center gap-1 text-[11px] text-slate-400">
      <span>模型</span>
      <select
        aria-label="模型"
        value={value}
        disabled={disabled || models.length === 0}
        onChange={(e) => onChange(e.target.value)}
        className="max-w-[220px] bg-slate-900 border border-slate-700 rounded px-2 py-1 text-[11px] text-slate-200 focus:outline-none focus:border-sky-500 disabled:opacity-40"
      >
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name} · {m.model}
          </option>
        ))}
      </select>
    </label>
  );
}
