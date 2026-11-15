import { chromium } from '../../front/node_modules/playwright-core/index.mjs';
import fs from 'node:fs';
const base = process.env.SEASONAL_CAPTURE_URL || 'http://127.0.0.1:3017';
if (!['localhost', '127.0.0.1'].includes(new URL(base).hostname)) throw Error('Local capture only');
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
fs.mkdirSync('work/seasonal-review', { recursive: true });
try {
  for (const view of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport: view, reducedMotion: 'reduce' });
    await context.route('**/api/**', route => route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }));
    const page = await context.newPage();
    await page.addInitScript(() => localStorage.setItem('omni_season_preferences', JSON.stringify({ mode: 'manual', season: 'autumn', motionEnabled: false })));
    await page.goto(base);
    await page.getByTestId('auth-username').waitFor();
    await page.locator('[data-asset-status="ready"]').waitFor({ timeout: 30000 });
    for (const [season, label] of [['spring', '春'], ['summer', '夏'], ['autumn', '秋'], ['winter', '冬']]) {
      await page.getByRole('button', { name: label, exact: true }).click();
      await page.locator(`[data-asset-status="ready"][data-season="${season}"]`).waitFor();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `work/seasonal-review/${season}-${view.name}.png`, fullPage: true });
      console.log(view.name, season, 'ready');
    }
    await context.close();
  }
} finally { await browser.close(); }
