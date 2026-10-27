import { useEffect, useState } from "react";
import { apiErrorText, getPlan, putPlan } from "../../api/client";
import { Markdown } from "../../lib/markdown";
import type { Task } from "../../types";
import { Button } from "../ui/Button";
import { Status } from "../ui/Status";
import { parsePlanToc, type TocItem } from "./planToc";

function PlanToc({ items }: { items: TocItem[] }) {
  if (items.length === 0) return null;
  return (
    <nav
      aria-label="计划目录"
      className="rounded-well bg-surface-well px-3 py-2"
    >
      <ul className="space-y-0.5">
        {items.map((item) => (
          <li key={item.line} className={item.level === 3 ? "pl-3" : undefined}>
            <a
              href={`#${item.slug}`}
              onClick={(e) => {
                e.preventDefault();
                document
                  .getElementById(item.slug)
                  ?.scrollIntoView({ block: "start" });
              }}
              className="flex min-h-6 items-center text-meta text-secondary hover:text-primary"
            >
              {item.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function PlanPane({ task }: { task: Task }) {
  const [text, setText] = useState("");
  const [version, setVersion] = useState(0);
  /** Text as loaded into the editor; save compares the fresh file against it. */
  const [loaded, setLoaded] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{
    text: string;
    version: number;
  } | null>(null);

  const editable = task.status === "INTERRUPTED" || task.status === "FAILED";

  useEffect(() => {
    setEditing(false);
    setNote(null);
    setConflict(null);
    setText("");
    setVersion(0);
    setLoaded("");
  }, [task.id]);

  useEffect(() => {
    if (editing) return;
    let cancelled = false;
    getPlan(task.id)
      .then((r) => {
        if (cancelled || !r.data) return;
        setText(r.data.text);
        setVersion(r.data.version);
        setLoaded(r.data.text);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [task.id, task.plan_version, task.status, editing]);

  const startEdit = () => {
    setLoaded(text);
    setEditing(true);
    setNote(null);
    setConflict(null);
  };

  const cancel = () => {
    setText(loaded);
    setEditing(false);
    setNote(null);
    setConflict(null);
  };

  const doPut = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await putPlan(task.id, text);
      if (r.data?.ok) {
        setVersion(r.data.version);
        setLoaded(text);
        setEditing(false);
        setConflict(null);
        setNote("已保存；下一条消息生效");
      } else {
        setNote(apiErrorText(r));
      }
    } catch {
      setNote("保存失败");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setNote(null);
    try {
      // GET has no hash, so "has the file changed since I opened the editor?"
      // is answered by re-reading the text and comparing it locally.
      try {
        const fresh = await getPlan(task.id);
        const freshText = fresh.data?.text ?? null;
        if (freshText !== null && freshText !== loaded) {
          setConflict({
            text: freshText,
            version: fresh.data?.version ?? version,
          });
          setNote("计划文件在编辑期间被外部修改；请选择载入新版或覆盖保存。");
          return;
        }
      } catch {
        // No fresh read: attempt the PUT; the server still owns the status gate.
      }
      const r = await putPlan(task.id, text);
      if (r.data?.ok) {
        setVersion(r.data.version);
        setLoaded(text);
        setEditing(false);
        setConflict(null);
        setNote("已保存；下一条消息生效");
      } else {
        setNote(apiErrorText(r));
      }
    } catch {
      setNote("保存失败");
    } finally {
      setBusy(false);
    }
  };

  const loadFresh = () => {
    if (!conflict) return;
    setText(conflict.text);
    setLoaded(conflict.text);
    setVersion(conflict.version);
    setConflict(null);
    setNote("已载入新版；继续编辑后再保存。");
  };

  const toc = parsePlanToc(text);
  const headingIds = new Map(toc.map((item) => [item.line, item.slug]));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex min-w-0 items-center gap-2">
        <span className="shrink-0 text-meta text-secondary">计划 v{version}</span>
        <Status status={task.status} live={false} />
        {editable && !editing && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto shrink-0"
            onClick={startEdit}
          >
            {text.trim() ? "编辑" : "编写计划"}
          </Button>
        )}
        {editing && (
          <>
            <Button
              variant="outline"
              size="sm"
              className="ml-auto shrink-0"
              onClick={save}
              disabled={busy}
            >
              {busy ? "保存中…" : "保存计划"}
            </Button>
            <Button size="sm" onClick={cancel} disabled={busy}>
              取消
            </Button>
          </>
        )}
      </div>

      {!editable && (
        <p className="text-meta text-tertiary">
          {task.status === "PENDING"
            ? "任务启动中；停下后可编辑计划。"
            : "任务运行中；停下后可编辑计划，改动在下一条消息生效。"}
        </p>
      )}

      {note && (
        <p role="status" className="text-meta text-secondary">
          {note}
        </p>
      )}

      {conflict && (
        <div className="flex flex-wrap items-center gap-2 rounded-well border border-state-warn/40 bg-state-warn/5 px-3 py-2">
          <span className="text-meta text-state-warn">
            文件已是第 {conflict.version} 版。
          </span>
          <Button variant="outline" size="sm" onClick={loadFresh}>
            载入新版
          </Button>
          <Button variant="danger" size="sm" onClick={doPut} disabled={busy}>
            覆盖保存
          </Button>
        </div>
      )}

      {editing ? (
        <textarea
          aria-label="计划文本"
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          className="min-h-[280px] w-full resize-y rounded-well border border-rule-strong bg-surface-well px-3 py-2 font-mono text-code text-primary"
        />
      ) : text.trim() ? (
        <>
          <PlanToc items={toc} />
          <Markdown headingIds={headingIds}>{text}</Markdown>
        </>
      ) : (
        <p className="text-body text-tertiary">
          {task.mode === "plan" && task.status === "RUNNING"
            ? "计划生成中…"
            : "还没有计划。停下后可以手动写一份，或切到计划模式问一次。"}
        </p>
      )}
    </div>
  );
}
