import { useAuthStore } from "../store/authStore";
import { Button, type ButtonSize } from "./ui/Button";

/**
 * #256: a 401 means the stored token expired. `AuthGuard` only checks that a
 * token *exists*, so reloading keeps the dead one and lands on the same 401 —
 * the real next step is dropping the token and logging in again
 * (design.md §7: say why + a next step that exists).
 */
export function ReauthAction({ size = "sm" }: { size?: ButtonSize }) {
  const logout = useAuthStore((s) => s.logout);
  return (
    <Button variant="outline" size={size} onClick={logout}>
      重新登录
    </Button>
  );
}
