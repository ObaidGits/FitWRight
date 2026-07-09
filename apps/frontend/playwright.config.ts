import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E config (Task 17.4).
 *
 * Exercises the running FitWright stack (frontend :3000 + backend :8000). By
 * default it reuses an already-running dev server; in CI set the servers up
 * first (or let `webServer` boot the frontend). The AI-heavy core path
 * (tailor → cover letter → export) runs only when RUN_AI_E2E=1 and a funded,
 * non-rate-limited LLM key is configured — otherwise those steps are skipped so
 * the suite stays green without burning provider quota.
 */
export default defineConfig({
  testDir: './e2e',
  // Gated hosted auth journeys need a pre-authenticated browser + a seeded 2nd
  // device; this global setup logs in through the real backend and persists
  // `storageState` — but ONLY when RUN_AUTH_E2E=1 (otherwise it is a no-op, so
  // the deterministic default run is untouched). See e2e/auth.setup.ts.
  globalSetup: require.resolve('./e2e/auth.setup.ts'),
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  timeout: 120_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run dev',
    url: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
