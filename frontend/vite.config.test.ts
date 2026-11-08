import { describe, expect, it } from "vitest";

import { backendProxyTarget, devProxy } from "./vite.config";

// D-41 (#259): proxy target follows the launcher's PORT and covers both /api
// and /health; the config is a function so this is a plain node assertion.
describe("vite dev proxy (#259 D-41)", () => {
  it("defaults to the backend on 127.0.0.1:8000", () => {
    expect(backendProxyTarget({})).toBe("http://127.0.0.1:8000");

    const proxy = devProxy({});
    expect(proxy["/api"].target).toBe("http://127.0.0.1:8000");
    expect(proxy["/health"].target).toBe("http://127.0.0.1:8000");
    expect(proxy["/api"].changeOrigin).toBe(true);
    expect(proxy["/health"].changeOrigin).toBe(true);
  });

  it("follows PORT exported by start.py --port", () => {
    expect(backendProxyTarget({ PORT: "9123" })).toBe("http://127.0.0.1:9123");
    expect(devProxy({ PORT: "9123" })["/api"].target).toBe(
      "http://127.0.0.1:9123",
    );
  });

  it("trims an empty PORT back to the default", () => {
    expect(backendProxyTarget({ PORT: "   " })).toBe("http://127.0.0.1:8000");
  });
});
