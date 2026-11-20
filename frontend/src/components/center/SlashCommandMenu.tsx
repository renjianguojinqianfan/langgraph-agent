import { cx } from "../../lib/cx";
import type { SlashCommandId, SlashCommandItem } from "../../lib/slashCommands";

interface SlashCommandMenuProps {
  id: string;
  items: SlashCommandItem[];
  activeIndex: number;
  onHighlight: (index: number) => void;
  onRun: (id: SlashCommandId) => void;
}

/**
 * #342: the `/` listbox anchored above the textarea. It never takes focus —
 * the textarea stays focused and points here with `aria-activedescendant`.
 * Mouse-down is cancelled for the same reason; a click still runs the option.
 */
export function SlashCommandMenu({
  id,
  items,
  activeIndex,
  onHighlight,
  onRun,
}: SlashCommandMenuProps) {
  return (
    <div
      id={id}
      role="listbox"
      aria-label="命令"
      className="absolute bottom-full left-0 right-0 z-30 mb-2 max-h-[min(320px,60vh)] overflow-y-auto overscroll-contain rounded-sheet border border-rule-strong bg-surface-sheet p-1 shadow-lg"
      onMouseDown={(event) => event.preventDefault()}
    >
      <div className="px-2 py-1 font-mono text-machine text-tertiary">命令</div>
      {items.length === 0 ? (
        <p role="status" className="px-2 py-2 text-meta text-tertiary">
          没有匹配的命令或技能
        </p>
      ) : (
        items.map((item, index) => (
          <div
            key={item.id}
            id={`${id}-${item.id}`}
            role="option"
            aria-selected={index === activeIndex}
            aria-disabled={!item.enabled}
            title={item.reason ?? item.description}
            onMouseEnter={() => onHighlight(index)}
            onClick={() => {
              if (item.enabled) onRun(item.id);
            }}
            className={cx(
              "flex min-w-0 items-baseline gap-2 rounded-control px-2 py-1.5",
              item.enabled ? "cursor-pointer" : "cursor-default opacity-60",
              index === activeIndex && "bg-surface-hover"
            )}
          >
            <span className="shrink-0 font-mono text-machine text-primary">
              {item.label}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-meta text-secondary">
                {item.description}
              </span>
              {!item.enabled && item.reason && (
                <span className="block truncate text-machine text-state-warn">
                  {item.reason}
                </span>
              )}
            </span>
          </div>
        ))
      )}
    </div>
  );
}
