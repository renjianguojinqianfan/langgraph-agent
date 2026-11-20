import { cx } from "../../lib/cx";
import type { SkillGroupView } from "../../lib/skillCatalog";
import type { SlashCommandId, SlashCommandItem } from "../../lib/slashCommands";

interface SlashCommandMenuProps {
  id: string;
  commands: SlashCommandItem[];
  /** #345: filtered skills and the group-only loading / empty / error face. */
  skillGroup: SkillGroupView;
  activeIndex: number;
  onHighlight: (index: number) => void;
  onRun: (id: SlashCommandId) => void;
  onPickSkill: (name: string) => void;
  onRetrySkills: () => void;
}

/**
 * #342/#345: the `/` listbox anchored above the textarea. It never takes
 * focus — the textarea stays focused and points here with
 * `aria-activedescendant`. Mouse-down is cancelled for the same reason; a
 * click still runs the option. Commands and skills share one flat keyboard
 * index (commands first), so ↑/↓/Enter/Tab keep #342's behaviour.
 */
export function SlashCommandMenu({
  id,
  commands,
  skillGroup,
  activeIndex,
  onHighlight,
  onRun,
  onPickSkill,
  onRetrySkills,
}: SlashCommandMenuProps) {
  const skillStart = commands.length;
  const noteStatus =
    skillGroup.status === "loading" ||
    skillGroup.status === "disabled" ||
    skillGroup.status === "empty" ||
    skillGroup.status === "error";
  const showSkillSection = skillGroup.items.length > 0 || noteStatus;
  const retryIndex = skillStart + skillGroup.items.length;
  const showNoMatch = commands.length === 0 && !showSkillSection;

  return (
    <div
      id={id}
      role="listbox"
      aria-label="命令与技能"
      className="absolute bottom-full left-0 right-0 z-30 mb-2 max-h-[min(320px,60vh)] overflow-y-auto overscroll-contain rounded-sheet border border-rule-strong bg-surface-sheet p-1 shadow-lg"
      onMouseDown={(event) => event.preventDefault()}
    >
      {commands.length > 0 && (
        <>
          <div className="px-2 py-1 font-mono text-machine text-tertiary">
            命令
          </div>
          {commands.map((item, index) => (
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
          ))}
        </>
      )}

      {showSkillSection && (
        <>
          <div className="px-2 py-1 font-mono text-machine text-tertiary">
            技能
          </div>
          {skillGroup.items.map((item, index) => {
            const row = skillStart + index;
            return (
              <div
                key={item.name}
                id={`${id}-skill-${index}`}
                role="option"
                aria-selected={row === activeIndex}
                title={`${item.name}${item.description ? ` · ${item.description}` : ""} · ${item.layerLabel}`}
                onMouseEnter={() => onHighlight(row)}
                onClick={() => onPickSkill(item.name)}
                className={cx(
                  "flex min-w-0 cursor-pointer items-baseline gap-2 rounded-control px-2 py-1.5",
                  row === activeIndex && "bg-surface-hover"
                )}
              >
                <span className="min-w-0 max-w-[45%] shrink truncate font-mono text-machine text-primary">
                  {item.name}
                </span>
                <span className="min-w-0 flex-1">
                  {item.description && (
                    <span className="block truncate text-meta text-secondary">
                      {item.description}
                    </span>
                  )}
                  <span className="block truncate font-mono text-machine text-tertiary">
                    {item.layerLabel}
                  </span>
                </span>
              </div>
            );
          })}
          {skillGroup.note && !skillGroup.canRetry && (
            <p role="status" className="px-2 py-2 text-meta text-tertiary">
              {skillGroup.note}
            </p>
          )}
          {skillGroup.canRetry && (
            <div
              id={`${id}-skills-retry`}
              role="option"
              aria-selected={retryIndex === activeIndex}
              title={skillGroup.note ?? undefined}
              onMouseEnter={() => onHighlight(retryIndex)}
              onClick={onRetrySkills}
              className={cx(
                "flex min-w-0 cursor-pointer items-baseline gap-2 rounded-control px-2 py-1.5",
                retryIndex === activeIndex && "bg-surface-hover"
              )}
            >
              <span className="shrink-0 font-mono text-machine text-primary">
                重试
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-meta text-secondary">
                  重新读取技能目录
                </span>
                {skillGroup.note && (
                  <span className="block truncate text-machine text-state-danger">
                    {skillGroup.note}
                  </span>
                )}
              </span>
            </div>
          )}
        </>
      )}

      {showNoMatch && (
        <p role="status" className="px-2 py-2 text-meta text-tertiary">
          没有匹配的命令或技能
        </p>
      )}
    </div>
  );
}
