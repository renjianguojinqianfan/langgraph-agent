import { useState } from "react";
import { cx } from "../../lib/cx";
import type { SubTask } from "../../types";
import { Icon } from "../ui/Icon";
import { SubagentPlate, subagentMeta } from "./SubagentPlate";

function SubagentDetail({ subtask }: { subtask: SubTask }) {
  const meta = subagentMeta(subtask.status);
  return (
    <div className="border-t border-rule px-4 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <span
          className="min-w-0 truncate text-body text-primary"
          title={subtask.name}
        >
          {subtask.name || "未命名子任务"}
        </span>
        <span className={cx("shrink-0 text-meta", meta.tone)}>{meta.label}</span>
        <span className="shrink-0 font-mono text-machine text-tertiary">
          {subtask.subtask_id}
        </span>
      </div>

      <div className="mt-2 text-meta text-tertiary">摘要</div>
      <p className="whitespace-pre-wrap text-body text-secondary">
        {subtask.summary.trim() || "（还没有摘要）"}
      </p>

      {subtask.error && (
        <p className="mt-1 text-meta text-state-danger">原因：{subtask.error}</p>
      )}

      <div className="mt-2 text-meta text-tertiary">
        产物{subtask.artifacts.length ? `（${subtask.artifacts.length}）` : ""}
      </div>
      {subtask.artifacts.length ? (
        <ul className="mt-0.5 flex flex-col gap-0.5">
          {subtask.artifacts.map((artifact) => (
            <li
              key={artifact}
              className="truncate font-mono text-machine text-secondary"
              title={artifact}
            >
              {artifact}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-body text-tertiary">（无产物）</p>
      )}

      <p className="mt-2 text-machine text-tertiary">
        这里只展示名字、状态、摘要与产物；内部过程与用量不暴露。
      </p>
    </div>
  );
}

/** Collapsible strip above the conversation; only build-mode tasks have sub-agents. */
export function SubagentStrip({ subtasks }: { subtasks: SubTask[] }) {
  const [open, setOpen] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);

  if (subtasks.length === 0) return null;
  const active = subtasks.find((s) => s.subtask_id === activeId) ?? null;

  return (
    <section aria-label="子智能体" className="shrink-0 border-b border-rule">
      <div className="flex items-center px-4 py-1.5">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 items-center gap-1.5 text-meta text-secondary hover:text-primary"
        >
          <Icon
            name="chevron"
            size={14}
            className={cx(
              "transition-transform duration-150",
              open && "rotate-180"
            )}
          />
          <span>子智能体</span>
          <span className="font-mono text-machine tabular-nums text-tertiary">
            {subtasks.length}
          </span>
        </button>
      </div>

      {open && (
        <>
          <div className="flex flex-wrap gap-1.5 px-4 pb-2">
            {subtasks.map((st) => (
              <SubagentPlate
                key={st.subtask_id}
                subtask={st}
                active={st.subtask_id === activeId}
                onToggle={() =>
                  setActiveId(activeId === st.subtask_id ? null : st.subtask_id)
                }
              />
            ))}
          </div>
          {active && <SubagentDetail subtask={active} />}
        </>
      )}
    </section>
  );
}
