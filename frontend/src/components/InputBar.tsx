import { useState } from "react";
import {
  apiErrorText,
  createTask,
  getTask,
  putMode,
  putTaskModel,
  sendMessage,
} from "../api/client";
import { useModelStore } from "../store/modelStore";
import { useTaskStore } from "../store/taskStore";
import { TaskMode } from "../types";
import { ModeSwitch } from "./ModeSwitch";
import { ModelSelect } from "./ModelSelect";
import { currentDir } from "../workspace";

export function InputBar() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  // Mode for a task that does not exist yet; an existing task carries its own.
  const [draftMode, setDraftMode] = useState<TaskMode>("build");
  const [draftModel, setDraftModel] = useState("");
  const [note, setNote] = useState<string | null>(null);

  const currentTaskId = useTaskStore((s) => s.currentTaskId);
  const task = useTaskStore((s) =>
    s.currentTaskId ? s.detail[s.currentTaskId] : undefined
  );
  const setCurrent = useTaskStore((s) => s.setCurrentTask);
  const upsert = useTaskStore((s) => s.upsertTask);
  const models = useModelStore((s) => s.models);
  const defaultModelId = useModelStore((s) => s.defaultId);

  const running = task?.status === "RUNNING" || task?.status === "PENDING";
  const parked = task?.status === "INTERRUPTED";
  const failed = task?.status === "FAILED";
  // A selected task whose snapshot has not arrived yet must not be treated as
  // "new task": sending would silently create a second task.
  const loading = !!currentTaskId && !task;
  const canSend = !busy && !loading && !!text.trim() && (!task || parked);
  const mode: TaskMode = task?.mode ?? draftMode;
  const selectedModel = task
    ? task.model || defaultModelId
    : draftModel || defaultModelId;

  const onMode = async (next: TaskMode) => {
    setNote(null);
    if (!task) {
      setDraftMode(next);
      return;
    }
    try {
      const r = await putMode(task.id, next);
      if (r?.data?.ok) {
        const fresh = await getTask(task.id);
        if (fresh.data) upsert(fresh.data);
        if (running) setNote("下一轮生效");
      } else {
        setNote(apiErrorText(r));
      }
    } catch {
      setNote("切换失败");
    }
  };

  const onModel = async (next: string) => {
    setNote(null);
    if (!task) {
      setDraftModel(next);
      return;
    }
    if (next === (task.model || defaultModelId)) return;
    try {
      const r = await putTaskModel(task.id, next);
      if (r.data?.ok) {
        const fresh = await getTask(task.id);
        if (fresh.data) upsert(fresh.data);
        if (running) setNote("下一轮生效");
      } else {
        setNote(apiErrorText(r));
      }
    } catch {
      setNote("切换失败");
    }
  };

  const onSubmit = async () => {
    const input = text.trim();
    if (!input || busy || loading) return;
    setBusy(true);
    setNote(null);
    try {
      if (!task) {
        const res = await createTask({
          input,
          mode: draftMode,
          workspace: currentDir() ?? undefined,
          model: draftModel || defaultModelId || undefined,
        });
        const id = res.data?.task_id;
        if (id) {
          setCurrent(id);
          // Only clear what was sent: a draft typed while the request was in
          // flight must survive.
          setText((prev) => (prev.trim() === input ? "" : prev));
        } else {
          setNote(apiErrorText(res));
        }
      } else if (parked) {
        // #139: one task = one conversation — speaking to a parked task
        // continues it in place (same thread_id / checkpoint). A 409 (no
        // checkpoint, still shutting down, …) must not eat the message: keep
        // the text and show the server's reason.
        const res = await sendMessage(task.id, input);
        if (res.data?.task) {
          upsert(res.data.task);
          setText((prev) => (prev.trim() === input ? "" : prev));
        } else {
          setNote(apiErrorText(res));
        }
      }
    } catch {
      setNote("发送失败");
    } finally {
      setBusy(false);
    }
  };

  const placeholder = loading
    ? "正在加载任务…"
    : !task
      ? "用自然语言描述一个任务，例如：联网查资料并写一份报告…"
      : parked
        ? "接着说一句，任务从这里继续…"
        : running
          ? "任务运行中…"
          : failed
            ? "任务出错，无法继续；请新建任务"
            : "…";

  return (
    <div className="border-t border-slate-800 p-3">
      <div className="flex items-center gap-2 mb-2">
        <ModeSwitch mode={mode} onChange={onMode} />
        <ModelSelect
          value={selectedModel}
          models={models}
          disabled={loading || busy}
          onChange={onModel}
        />
        {note && <span className="text-[11px] text-slate-400">{note}</span>}
      </div>
      <div className="flex gap-2">
        <textarea
          className="flex-1 resize-none bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-sky-500 disabled:opacity-50"
          rows={2}
          placeholder={placeholder}
          value={text}
          disabled={!!task && !parked}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSubmit();
          }}
        />
        <button
          onClick={onSubmit}
          disabled={!canSend}
          className="px-4 rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-40 text-white text-sm"
        >
          发送
        </button>
      </div>
      <div className="text-[11px] text-slate-500 mt-1">
        {failed
          ? "任务出错后不能续说；新建任务重新开始"
          : "Ctrl/⌘ + Enter 发送"}
        {currentTaskId ? "　·　这一轮没有工具调用即停下，之后可以继续说" : ""}
      </div>
    </div>
  );
}
