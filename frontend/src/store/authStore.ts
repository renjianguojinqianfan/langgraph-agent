import { create } from "zustand";
import { loginRequest } from "../api/client";

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
  login: (secret: string) => Promise<boolean>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: getStoredToken(),
  authEnabled: null,

  setAuthEnabled: (v) => set({ authEnabled: v }),

  login: async (secret) => {
    try {
      const res = await loginRequest(secret);
      const token = res.data?.token;
      if (res.data?.ok && token) {
        try {
          localStorage.setItem(AUTH_TOKEN_KEY, token);
        } catch {
          /* storage unavailable — keep in memory only */
        }
        set({ token });
        return true;
      }
      return false;
    } catch {
      return false;
    }
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
