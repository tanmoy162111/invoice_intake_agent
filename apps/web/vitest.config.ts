import { defineConfig } from "vitest/config";

// The browser tests in e2e/ run with Playwright (`make e2e`), not here.
export default defineConfig({
  test: { exclude: ["e2e/**", "node_modules/**", ".next/**"] },
});
