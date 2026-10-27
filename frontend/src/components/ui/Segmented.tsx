import { cx } from "../../lib/cx";

interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

interface SegmentedProps<T extends string> {
  options: Array<SegmentedOption<T>>;
  value: T;
  onChange: (value: T) => void;
  label: string;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: SegmentedProps<T>) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex items-center gap-0.5 rounded-control bg-surface-well p-0.5"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            title={option.title}
            aria-pressed={selected}
            onClick={() => onChange(option.value)}
            className={cx(
              "rounded-control px-2 py-1 text-meta transition-colors duration-150",
              selected
                ? "text-primary shadow-[inset_0_-2px_0_var(--color-primary)]"
                : "text-tertiary hover:text-secondary"
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
