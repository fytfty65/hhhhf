import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const fixturePoster = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="9"><rect width="16" height="9" fill="#456"/></svg>';

// Local mocked authentication only; never create accounts on a deployed gateway.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/**', async route => {
    if (route.request().url().endsWith('/login')) {
      await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: '账号或密码错误' }) });
    } else {
      await route.fulfill({ status: 401, contentType: 'application/json', body: '{}' });
    }
  });
  await page.addInitScript(() => {
    localStorage.setItem('omni_season_preferences', JSON.stringify({ season: 'autumn', mode: 'manual', motionEnabled: false }));
  });
});

test('season switching preserves credentials and authentication errors', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('auth-username').fill('season-traveler');
  await page.getByTestId('auth-password').fill('not-a-real-password');
  await page.getByRole('button', { name: '冬', exact: true }).click();
  await expect(page.getByRole('button', { name: '冬', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('auth-username')).toHaveValue('season-traveler');
  await expect(page.getByTestId('auth-password')).toHaveValue('not-a-real-password');
  await page.getByTestId('auth-submit').click();
  await expect(page.getByText('账号或密码错误', { exact: true })).toBeVisible();
  await expect(page.getByTestId('auth-submit')).toBeEnabled();
});

test('mobile registration and reset remain reachable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto('/');
  await page.getByTestId('auth-register-tab').click();
  await expect(page.getByTestId('auth-nickname')).toBeVisible();
  await page.getByTestId('auth-submit').scrollIntoViewIfNeeded();
  await expect(page.getByTestId('auth-submit')).toBeInViewport();
  await page.getByTestId('auth-login-tab').click();
  await page.getByTestId('auth-forgot').click();
  await expect(page.getByTestId('reset-username')).toBeVisible();
  await expect(page.getByTestId('reset-request')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: '../work/seasonal-review/mobile-reset.png', fullPage: true });
});

test('failed season assets do not interrupt the form', async ({ page }) => {
  await page.route('**/hero/v1/**', route => route.abort());
  await page.goto('/');
  await page.getByTestId('auth-username').fill('still-usable');
  await page.getByRole('button', { name: '冬', exact: true }).click();
  await expect(page.getByTestId('auth-username')).toHaveValue('still-usable');
  await expect(page.getByTestId('auth-submit')).toBeEnabled();
  await expect(page.locator('[data-asset-status="fallback"]')).toBeVisible();
  await expect(page.getByRole('button', { name: '场景暂未加载 · 重试' })).toBeVisible();
  await page.getByRole('button', { name: '场景暂未加载 · 重试' }).click();
  await expect(page.getByTestId('auth-username')).toHaveValue('still-usable');
});

test('failed target preserves displayed season and retry commits it', async ({ page }) => {
  let failWinter = true;
  await page.route('**/hero/v1/**', route => route.request().url().includes('winter') && failWinter
    ? route.abort() : route.fulfill({ contentType: 'image/svg+xml', body: fixturePoster }));
  await page.goto('/');
  await expect(page.locator('[data-asset-status]')).toHaveAttribute('data-asset-status', 'ready');
  await expect(page.getByRole('button', { name: '秋', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '冬', exact: true }).click();
  await expect(page.locator('[data-asset-status]')).toHaveAttribute('data-asset-status', 'fallback');
  await expect(page.getByRole('button', { name: '秋', exact: true })).toHaveAttribute('aria-pressed', 'true');
  failWinter = false;
  await page.getByRole('button', { name: '场景暂未加载 · 重试' }).click();
  await expect(page.getByRole('button', { name: '冬', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('late image decode cannot override the latest requested season', async ({ page }) => {
  let releaseWinter: (() => void) | undefined;
  const winterGate = new Promise<void>(resolve => { releaseWinter = resolve; });
  await page.route('**/hero/v1/**', async route => {
    if (route.request().url().includes('winter')) await winterGate;
    await route.fulfill({ contentType: 'image/svg+xml', body: fixturePoster });
  });
  await page.goto('/');
  await expect(page.locator('[data-asset-status]')).toHaveAttribute('data-asset-status', 'ready');
  await page.getByRole('button', { name: '冬', exact: true }).click();
  await page.getByRole('button', { name: '夏', exact: true }).click();
  await expect(page.getByRole('button', { name: '夏', exact: true })).toHaveAttribute('aria-pressed', 'true');
  releaseWinter?.();
  await page.waitForLoadState('networkidle');
  await expect(page.getByRole('button', { name: '夏', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('demo uses a fresh 24-second interval after input focus pauses it', async ({ page }) => {
  await page.clock.install();
  await page.route('**/hero/v1/**', route => route.fulfill({ contentType: 'image/svg+xml', body: fixturePoster }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: '秋', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: '演示四季' }).click();
  await page.getByTestId('auth-username').focus();
  await page.clock.fastForward(48_000);
  await expect(page.getByRole('button', { name: '秋', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('heading', { name: '旅行者登录' }).click();
  await page.clock.fastForward(23_000);
  await expect(page.getByRole('button', { name: '秋', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.clock.fastForward(1_000);
  await expect(page.getByRole('button', { name: '冬', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('two-stage password reset preserves request contracts', async ({ page }) => {
  await page.route('**/api/auth/password/forgot', async route => {
    expect(route.request().postDataJSON()).toEqual({ username: 'traveler' });
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ reset_token: 'test-token' }) });
  });
  await page.route('**/api/auth/password/reset', async route => {
    expect(route.request().postDataJSON()).toEqual({ token: 'test-token', new_password: 'new-test-password' });
    await route.fulfill({ contentType: 'application/json', body: '{}' });
  });
  await page.goto('/');
  await page.getByTestId('auth-forgot').click();
  await page.getByTestId('reset-username').fill('traveler');
  await page.getByTestId('reset-request').click();
  await expect(page.getByTestId('reset-token')).toHaveValue('test-token');
  await page.getByTestId('reset-new-password').fill('new-test-password');
  await page.getByTestId('reset-submit').click();
  await expect(page.getByRole('status')).toHaveText('密码已重置，请使用新密码登录');
  await expect(page.getByTestId('auth-password')).toHaveValue('');
});

test('responsive form matrix stays scrollable and inside the viewport', async ({ page }) => {
  for (const [width, height] of [[360, 800], [390, 844], [768, 1024], [1366, 768], [1440, 900], [1920, 1080], [844, 390]]) {
    await page.setViewportSize({ width, height });
    await page.goto('/');
    await page.getByTestId('auth-register-tab').click();
    await page.getByTestId('auth-submit').scrollIntoViewIfNeeded();
    await expect(page.getByTestId('auth-submit')).toBeInViewport();
    const sizes = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: innerWidth }));
    expect(sizes.document, `${width}x${height} horizontal overflow`).toBeLessThanOrEqual(sizes.viewport);
  }
});

test('portal controls and form meet critical accessibility rules', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('auth-username').waitFor();
  const results = await new AxeBuilder({ page }).withRules(['button-name', 'label', 'aria-valid-attr', 'aria-valid-attr-value', 'color-contrast']).analyze();
  expect(results.violations.map(item => ({ rule: item.id, nodes: item.nodes.map(node => node.target) }))).toEqual([]);
});

test('reduced motion keeps scene static and controls keyboard accessible', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const winter = page.getByRole('button', { name: '冬', exact: true });
  await winter.focus();
  await page.keyboard.press('Enter');
  await expect(winter).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-asset-status]')).toHaveAttribute('data-motion', 'false');
});
