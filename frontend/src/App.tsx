import { useEffect } from "react";
import { AuthGuard } from "./components/AuthGuard";
import { TaskView } from "./pages/TaskView";
import { healthUrl } from "./api/client";
import { useAuthStore } from "./store/authStore";

export default function App() {
  const setAuthEnabled = useAuthStore((s) => s.setAuthEnabled);

  // P1 item 5: /health reports auth_enabled so the frontend can decide whether
  // to force a login page. Failures default to disabled (local demo).
  useEffect(() => {
    fetch(healthUrl())
      .then((r) => r.json())
      .then((h) => setAuthEnabled(Boolean(h?.auth_enabled)))
      .catch(() => setAuthEnabled(false));
  }, [setAuthEnabled]);

  return (
    <div className="h-full">
      <AuthGuard>
        <TaskView />
      </AuthGuard>
    </div>
  );
}
