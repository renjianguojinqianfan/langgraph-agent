import { useState } from "react";
import { useAuthStore } from "../store/authStore";
import type { LoginOutcome } from "../lib/failureStates";

/** #256: each failure says what happened and what to do next (§7 错). */
const FAILURE_COPY: Record<Exclude<LoginOutcome, "ok">, string> = {
  "wrong-secret": "口令错误，请重试。",
  unreachable: "连不上后台（网络或服务端错误）；确认服务在运行后重试。",
};

export function LoginPage() {
  const login = useAuthStore((s) => s.login);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<LoginOutcome>("ok");
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!secret.trim()) return;
    setBusy(true);
    setError("ok");
    const outcome = await login(secret.trim());
    setBusy(false);
    setError(outcome);
  };

  return (
    <div className="flex h-full items-center justify-center bg-surface-page">
      <form
        onSubmit={onSubmit}
        className="w-[360px] max-w-[90vw] rounded-dialog border border-rule bg-surface-column p-6"
      >
        <h1 className="mb-1 text-title text-primary">LangGraph 自主任务 Agent</h1>
        <p className="mb-4 text-body text-secondary">请输入访问口令以继续</p>
        <input
          type="password"
          aria-label="访问口令"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          className="mb-3 w-full rounded-control border border-rule bg-surface-well px-3 py-2 text-body text-primary placeholder:text-tertiary"
          placeholder="口令"
          autoFocus
        />
        {error !== "ok" && (
          <div role="alert" className="mb-2 text-body text-state-danger">
            {FAILURE_COPY[error]}
          </div>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-control bg-signal-now py-2 text-body text-on-signal transition-colors duration-150 hover:bg-signal-now/90 disabled:opacity-50"
        >
          {busy ? "登录中…" : "登录"}
        </button>
      </form>
    </div>
  );
}
