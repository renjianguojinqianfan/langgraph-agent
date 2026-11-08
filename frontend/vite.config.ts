import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// D-41 (#259): the dev proxy must follow the backend port.
//
// start.py exports PORT (and backend.config reads the same variable for its
// own-origin CORS list), so this reads PORT too. That is the one deliberate
// deviation from D-41's literal "BACKEND_PORT ?? 8000": this repo already had
// PORT wired end to end; adding a second variable would let the two disagree.
// Standalone `npm run dev` (no launcher) falls back to 8000.
const DEFAULT_BACKEND_PORT = 8000;

export type ProxyEnv = Record<string, string | undefined>;

function processEnv(): ProxyEnv {
  // No Node type definitions in this project (zero new deps): read the env
  // through globalThis instead of declaring the Node "process" global.
  const proc = (
    globalThis as {
      process?: { env?: ProxyEnv };
    }
  ).process;
  return proc?.env ?? {};
}

function parseBackendPort(raw: string | undefined): number {
  // L4 (#259): only a positive integer in the valid port range is a port;
  // anything else (empty, "abc", "0", "70000") falls back to 8000 instead of
  // building a broken proxy URL.
  const text = raw?.trim() ?? "";
  if (!/^[0-9]+$/.test(text)) {
    return DEFAULT_BACKEND_PORT;
  }
  const port = Number(text);
  return port >= 1 && port <= 65535 ? port : DEFAULT_BACKEND_PORT;
}

export function backendProxyTarget(env: ProxyEnv = processEnv()): string {
  return `http://127.0.0.1:${parseBackendPort(env.PORT)}`;
}

// /health is proxied too: it is outside /api but the dev page and manual
// checks use it (readiness), so leaving it out makes the dev origin lie.
export function devProxy(env: ProxyEnv = processEnv()) {
  const target = backendProxyTarget(env);
  return {
    "/api": { target, changeOrigin: true },
    "/health": { target, changeOrigin: true },
  };
}

export default defineConfig(() => ({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: devProxy(),
  },
}));
