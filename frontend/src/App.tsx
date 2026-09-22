import { useEffect } from "react";
import { AuthGuard } from "./components/AuthGuard";
import { TaskView } from "./pages/TaskView";
import { healthUrl } from "./api/client";
import { useAuthStore } from "./store/authStore";
import { PrototypeApp } from "./prototype/PrototypeApp";

// PROTOTYPE ONLY (branch prototype/frontend-redesign): open the app with
// ?prototype=1 to evaluate the workbench redesign variants instead of the app.
const PROTOTYPE_MODE = new URLSearchParams(window.location.search).has("prototype");

export default function App() {
  const setAuthEnabled = useAuthStore((s) => s.setAuthEnabled);

  // P1 item 5: /health reports auth_enabled so the frontend can decide whether
  // to force a login page. Failures default to disabled (local demo).
  useEffect(() => {
    if (PROTOTYPE_MODE) return;
    fetch(healthUrl())
      .then((r) => r.json())
      .then((h) => setAuthEnabled(Boolean(h?.auth_enabled)))
      .catch(() => setAuthEnabled(false));
  }, [setAuthEnabled]);

  if (PROTOTYPE_MODE) {
    return (
      <div className="h-full">
        <PrototypeApp />
      </div>
    );
  }

  return (
    <div className="h-full">
      <AuthGuard>
        <TaskView />
      </AuthGuard>
    </div>
  );
}
