import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['line'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL || `http://localhost:${process.env.E2E_PORT || '3001'}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Avoid requiring a separate ffmpeg download for local smoke runs. CI
    // keeps failure video artifacts for deeper diagnosis.
    video: process.env.CI ? 'retain-on-failure' : 'off',
  },
  webServer: {
    command: `node ./node_modules/next/dist/bin/next start --port ${process.env.E2E_PORT || '3001'}`,
    url: `http://localhost:${process.env.E2E_PORT || '3001'}`,
    reuseExistingServer: true,
    timeout: 120_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 3_000 },
  },
  projects: [{
    name: 'chromium',
    use: {
      ...devices['Desktop Chrome'],
      // Local environments may already have Chrome installed without the
      // Playwright browser bundle. CI can omit this and use its managed build.
      ...(process.env.E2E_CHROMIUM_PATH ? { launchOptions: { executablePath: process.env.E2E_CHROMIUM_PATH } } : {}),
    },
  }],
});
