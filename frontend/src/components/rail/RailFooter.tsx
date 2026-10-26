import { useAuthStore } from "../../store/authStore";
import { useTaskStore } from "../../store/taskStore";
import { Button, IconButton } from "../ui/Button";

export function RailFooter() {
  const authEnabled = useAuthStore((s) => s.authEnabled);
  const logout = useAuthStore((s) => s.logout);
  const setSettingsOpen = useTaskStore((s) => s.setSettingsOpen);

  return (
    <footer className="flex shrink-0 items-center justify-between gap-2 border-t border-rule px-3 py-2">
      <span
        className="truncate font-mono text-machine text-tertiary"
        title="单后台；交接提示本期不做"
      >
        共享后台
      </span>
      <div className="flex shrink-0 items-center gap-1">
        {authEnabled && (
          <Button size="sm" variant="quiet" onClick={logout}>
            登出
          </Button>
        )}
        <IconButton
          size="sm"
          icon="gear"
          aria-label="设置"
          onClick={() => setSettingsOpen(true)}
        />
      </div>
    </footer>
  );
}
