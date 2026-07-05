import { defineConfig, devices } from '@playwright/test'

// Tests mock every /api/* call (see e2e/fixtures.ts), so this never talks to
// the real FastAPI backend or a real Gmail-derived database - it only needs
// the frontend itself running. `webServer` starts/stops the preview server
// for the test run only; it is not a persistent dev server left running
// after `npm run test:e2e` exits.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'npm run preview -- --port 4173',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
