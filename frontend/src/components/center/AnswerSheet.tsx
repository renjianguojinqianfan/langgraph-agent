import { Markdown } from "../../lib/markdown";
import { profileName, useModelStore } from "../../store/modelStore";
import type { Task } from "../../types";

export function AnswerSheet({ task, content }: { task: Task; content: string }) {
  const models = useModelStore((s) => s.models);
  const defaultId = useModelStore((s) => s.defaultId);
  const modelLabel = task.model ? profileName(models, task.model) : "全局默认";
  const toolCount = task.steps.reduce((n, step) => n + step.tool_calls.length, 0);
  const empty = !content.trim();

  return (
    <article className="rounded-sheet bg-surface-sheet px-4 py-3 shadow-sheet">
      <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-tertiary">
        <span className="min-w-0 truncate" title={modelLabel}>
          {modelLabel}
        </span>
        <span className="flex shrink-0 items-center gap-1">
          累计工具
          <span className="font-mono text-machine tabular-nums">{toolCount}</span>
          次
        </span>
      </div>
      {empty ? (
        <p className="text-body text-tertiary">（空回答）</p>
      ) : (
        <Markdown>{content}</Markdown>
      )}
    </article>
  );
}
