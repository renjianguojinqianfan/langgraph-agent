import { useEffect } from "react";
import { AuthGuard } from "./components/AuthGuard";
import { SettingsDialog } from "./components/settings/SettingsDialog";
import { TaskView } from "./pages/TaskView";
import { healthUrl } from "./api/client";
import { useAuthStore } from "./store/authStore";
import { useTaskStore } from "./store/taskStore";

export default function App() {
  const setAuthEnabled = useAuthStore((s) => s.setAuthEnabled);
  const settingsOpen = useTaskStore((s) => s.settingsOpen);
  const setSettingsOpen = useTaskStore((s) => s.setSettingsOpen);

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
        {/* TaskView stays mounted: opening settings must not discard an
            unsent message or drop the live task stream. */}
        <TaskView />
        <SettingsDialog
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
        />
      </AuthGuard>
    </div>
  );
}
