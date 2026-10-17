import { TaskMode } from "../types";

const MODES: Array<{ value: TaskMode; label: string; title: string }> = [
  { value: "plan", label: "计划", title: "只读工具；产出可改的计划" },
  { value: "build", label: "执行", title: "全部已挂载工具；危险动作由规则表当场拒绝" },
];

/**
 * The sticky per-task mode switch (ADR 0008 decision 3).
 *
 * Default is build; the choice sticks until it is flipped back. For a task
 * that is already running the backend applies the new mode on the next round.
 */
export function ModeSwitch({
  mode,
  disabled,
  onChange,
}: {
  mode: TaskMode;
  disabled?: boolean;
  onChange: (mode: TaskMode) => void;
}) {
  return (
    <div className="inline-flex rounded border border-slate-700 overflow-hidden text-[11px]">
      {MODES.map((m) => (
        <button
          key={m.value}
          title={m.title}
          onClick={() => onChange(m.value)}
          disabled={disabled}
          className={`px-2 py-1 transition disabled:opacity-40 ${
            mode === m.value
              ? "bg-sky-600 text-white"
              : "bg-slate-900 text-slate-400 hover:text-slate-200"
          }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
