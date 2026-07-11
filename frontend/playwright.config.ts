import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration for the FinancialGenie Mapping Editor.
 *
 * Prerequisites:
 *   - Backend running on http://localhost:8765
 *   - Frontend running on http://localhost:5180
 *
 * The tests do NOT start the servers — run them separately before testing:
 *   cd /path/to/financialgenie && python3 backend/server.py &
 *   cd frontend && npm run dev &
 *
 * Then: npm run test:e2e
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [["html", { open: "never" }], ["list"]],
  timeout: 30_000,
  expect: {
    // Tolerant screenshot comparison — anti-aliasing and font rendering
    // differences across OSes should not break tests.
    toHaveScreenshot: { maxDiffPixelRatio: 0.05 },
  },
  use: {
    baseURL: "http://localhost:5180",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
