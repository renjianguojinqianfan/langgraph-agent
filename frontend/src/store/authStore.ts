import { create } from "zustand";
import { loginAttempt } from "../api/client";
import { loginOutcome, type LoginOutcome } from "../lib/failureStates";

// localStorage key must match backend/services/auth.py:TOKEN_STORAGE_KEY.
export const AUTH_TOKEN_KEY = "lga_auth_token";

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
}

interface AuthState {
  token: string | null;
  /** null = not yet known; undefined/false = auth disabled on the backend. */
  authEnabled: boolean | null;
  setAuthEnabled: (v: boolean) => void;
  /** #256: "wrong-secret" and "unreachable" are different failures. */
  login: (secret: string) => Promise<LoginOutcome>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: getStoredToken(),
  authEnabled: null,

  setAuthEnabled: (v) => set({ authEnabled: v }),

  login: async (secret) => {
    const attempt = await loginAttempt(secret);
    const outcome = loginOutcome(attempt.status, attempt.data?.ok === true);
    if (outcome === "ok" && attempt.data) {
      try {
        localStorage.setItem(AUTH_TOKEN_KEY, attempt.data.token);
      } catch {
        /* storage unavailable — keep in memory only */
      }
      set({ token: attempt.data.token });
    }
    return outcome;
  },

  logout: () => {
    try {
      localStorage.removeItem(AUTH_TOKEN_KEY);
    } catch {
      /* ignore */
    }
    set({ token: null });
  },
}));
