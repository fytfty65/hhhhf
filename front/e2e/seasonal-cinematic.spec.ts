import { test, expect } from '@playwright/test';

test('cinematic assets render in all seasons and form does not overlap footer', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => localStorage.setItem('omni_season_preferences', JSON.stringify({ season: 'spring', mode: 'manual', motionEnabled: false })));
  await page.goto('/');
  for (const [season, label] of [['spring','春'],['summer','夏'],['autumn','秋'],['winter','冬']]) {
    await page.getByRole('button', { name: label, exact: true }).click();
    await expect(page.locator('[data-asset-status]')).toHaveAttribute('data-season', season);
    await expect(page.locator('[data-asset-status]')).toHaveAttribute('data-asset-status', 'ready');
    await page.screenshot({ path: `../work/seasonal-review/${season}-desktop.png`, fullPage: true });
  }
  await page.getByTestId('auth-register-tab').click();
  const panel = await page.locator('#portal-login').boundingBox();
  const footer = await page.locator('footer').boundingBox();
  expect(panel!.y + panel!.height).toBeLessThanOrEqual(footer!.y);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '春', exact: true }).click();
  await expect(page.locator('[data-asset-status]')).toHaveAttribute('data-season','spring');
  await page.screenshot({ path:'../work/seasonal-review/spring-mobile.png', fullPage:true });
});
