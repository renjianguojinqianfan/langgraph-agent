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
    <div className="h-full flex items-center justify-center bg-slate-950">
      <form
        onSubmit={onSubmit}
        className="w-[360px] max-w-[90vw] bg-panel border border-slate-700 rounded-lg p-6 shadow-xl"
      >
        <h1 className="text-lg font-semibold mb-1">LangGraph 自主任务 Agent</h1>
        <p className="text-sm text-slate-400 mb-4">请输入访问口令以继续</p>
        <input
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm mb-3 focus:outline-none focus:border-sky-500"
          placeholder="口令"
          autoFocus
        />
        {error && <div className="text-red-400 text-sm mb-2">{error}</div>}
        <button
          type="submit"
          disabled={busy}
          className="w-full bg-sky-600 hover:bg-sky-500 disabled:opacity-50 rounded py-2 text-sm text-white"
        >
          {busy ? "登录中…" : "登录"}
        </button>
      </form>
    </div>
  );
}
