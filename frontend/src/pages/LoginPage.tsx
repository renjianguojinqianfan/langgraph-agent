import { useState } from "react";
import { useAuthStore } from "../store/authStore";

export function LoginPage() {
  const login = useAuthStore((s) => s.login);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!secret.trim()) return;
    setBusy(true);
    setError("");
    const ok = await login(secret.trim());
    setBusy(false);
    if (!ok) setError("口令错误，请重试");
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
        {error && <div className="mb-2 text-body text-state-danger">{error}</div>}
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
