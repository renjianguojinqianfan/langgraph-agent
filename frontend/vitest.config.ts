import { defineConfig } from "vitest/config";

// Pure-logic tests only (store reducer / decisions): node environment, no DOM.
// Explicit imports (`import { describe } from "vitest"`) — globals stay off.
export default defineConfig({
  test: {
    environment: "node",
  },
});
