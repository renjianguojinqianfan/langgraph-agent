import { ReactNode } from "react";
import { useAuthStore } from "../store/authStore";
import { LoginPage } from "../pages/LoginPage";

/**
 * Route guard: when the backend has auth enabled and no token is stored, the
 * user is redirected to the login page. With auth disabled (local demo) it
 * simply renders children (zero friction).
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const token = useAuthStore((s) => s.token);

  if (authEnabled === null) {
    // /health has not been fetched yet.
    return (
      <div className="h-full flex items-center justify-center text-slate-500">
        加载中…
      </div>
    );
  }
  if (authEnabled && !token) {
    return <LoginPage />;
  }
  return <>{children}</>;
}
