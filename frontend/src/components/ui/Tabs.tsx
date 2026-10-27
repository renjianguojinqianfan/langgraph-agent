import { useRef, type KeyboardEvent } from "react";
import { cx } from "../../lib/cx";

export interface TabItem<T extends string> {
  id: T;
  label: string;
}

interface TabsProps<T extends string> {
  label: string;
  tabs: ReadonlyArray<TabItem<T>>;
  value: T;
  onChange: (id: T) => void;
  /** Prefix for `tab` / `tabpanel` ids; both sides must use the same one. */
  baseId: string;
}

export function Tabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  baseId,
}: TabsProps<T>) {
  const refs = useRef(new Map<T, HTMLButtonElement>());

  const select = (id: T) => {
    onChange(id);
    refs.current.get(id)?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = tabs.findIndex((t) => t.id === value);
    if (at < 0) return;
    let next: number | null = null;
    if (e.key === "ArrowRight") next = (at + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (at - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next === null) return;
    e.preventDefault();
    select(tabs[next].id);
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      className="flex items-center gap-1"
      onKeyDown={onKeyDown}
    >
      {tabs.map((t) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`${baseId}-tab-${t.id}`}
            aria-controls={`${baseId}-panel-${t.id}`}
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            ref={(el) => {
              if (el) refs.current.set(t.id, el);
              else refs.current.delete(t.id);
            }}
            onClick={() => onChange(t.id)}
            className={cx(
              "relative h-9 px-2.5 text-meta transition-colors duration-150",
              active
                ? "text-primary after:absolute after:inset-x-1.5 after:bottom-0 after:h-[2px] after:bg-primary"
                : "text-tertiary hover:text-secondary"
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
