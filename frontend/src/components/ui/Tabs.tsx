import { useEffect, useRef, type KeyboardEvent } from "react";
import { cx } from "../../lib/cx";
import { Icon } from "./Icon";

export interface TabItem<T extends string> {
  id: T;
  label: string;
  /** Full value for a truncated label (file paths). */
  title?: string;
  /** Present on closable file tabs, rendered inside the tab chip (dsh shape). */
  onClose?: () => void;
}

interface TabsProps<T extends string> {
  label: string;
  tabs: ReadonlyArray<TabItem<T>>;
  value: T;
  onChange: (id: T) => void;
  /** Prefix for `tab` / `tabpanel` ids; both sides must use the same one. */
  baseId: string;
}

function domId(baseId: string, kind: "tab" | "panel", id: string): string {
  // Paths can carry spaces and slashes; encode them into a valid DOM id while
  // keeping the fixed panes' ids unchanged.
  return `${baseId}-${kind}-${encodeURIComponent(id)}`;
}

/** Panel-side counterpart of the id a tab button points at. */
export function tabPanelId(baseId: string, id: string): string {
  return domId(baseId, "panel", id);
}

/** Id of the tab chip itself (used to restore focus after closing a tab). */
export function tabButtonId(baseId: string, id: string): string {
  return domId(baseId, "tab", id);
}

export function Tabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  baseId,
}: TabsProps<T>) {
  const refs = useRef(new Map<T, HTMLDivElement>());

  const select = (id: T) => {
    onChange(id);
    refs.current.get(id)?.focus();
  };

  // Keep the active chip in view when many files are open.
  useEffect(() => {
    refs.current.get(value)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [value]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // Keys on the nested close control are that control's, not the tab's.
    if ((e.target as HTMLElement).getAttribute("role") !== "tab") return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onChange(value);
      return;
    }
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
      className="flex items-center gap-1 whitespace-nowrap"
      onKeyDown={onKeyDown}
    >
      {tabs.map((t) => {
        const active = t.id === value;
        return (
          <div
            key={t.id}
            role="tab"
            id={tabButtonId(baseId, t.id)}
            aria-controls={tabPanelId(baseId, t.id)}
            aria-selected={active}
            aria-label={t.label}
            tabIndex={active ? 0 : -1}
            title={t.title}
            ref={(el) => {
              if (el) refs.current.set(t.id, el);
              else refs.current.delete(t.id);
            }}
            onClick={() => onChange(t.id)}
            className={cx(
              "relative flex h-9 cursor-pointer items-center px-2.5 text-meta transition-colors duration-150",
              t.onClose && "pr-7",
              active
                ? "text-primary after:absolute after:bottom-0 after:h-[2px] after:bg-primary after:inset-x-1.5"
                : "text-tertiary hover:text-secondary"
            )}
          >
            <span className="block max-w-[140px] truncate">{t.label}</span>
            {t.onClose && (
              <button
                type="button"
                aria-label={`关闭 ${t.title ?? t.label} 预览`}
                title="关闭预览"
                onClick={(event) => {
                  event.stopPropagation();
                  t.onClose?.();
                }}
                className="absolute right-0.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-control text-tertiary hover:bg-surface-hover hover:text-primary"
              >
                <Icon name="close" size={12} />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
