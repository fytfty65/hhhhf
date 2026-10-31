import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  // 90s：主链路用例内部本身就有 10s+20s+20s 的等待（入房→推演→共识解释），
  // 6 个 worker 并行跑时 45s 会被累计等待吃满，出现过"页面其实已经渲染、用例却超时"的假红。
  timeout: 90_000,
  // 断言默认只等 5s：并行跑满负载时，地图绘制与面板淡入会被 JS 线程挤到 5s 之外，
  // 于是"页面其实已经渲染、断言却报超时"（全球风险地图、规划治理两处最常出现）。
  // 统一抬到 12s，不改变失败语义，只把机器负载抖动从判定里排除。
  expect: { timeout: 12_000 },
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
