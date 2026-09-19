import { test, expect } from '@playwright/test';

const user = {
  id: 'e2e-user', username: 'e2e-user', nickname: 'E2E 旅行者',
  avatarSeed: 'e2e-user', avatarUrl: '', signature: '', token: 'e2e-token', isLoggedIn: true,
};

async function expectNoHorizontalOverflow(page: import('@playwright/test').Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport + 1);
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport + 1);
}

test('注册/登录、建房、入房、AI 推演、预算与导出主链路', async ({ page }) => {
  const renderLifecycleErrors: string[] = [];
  page.on('console', message => {
    if (message.type() === 'error' && /synchronously unmount a root|React was already rendering/i.test(message.text())) {
      renderLifecycleErrors.push(message.text());
    }
  });
  page.on('pageerror', error => {
    if (/synchronously unmount a root|React was already rendering/i.test(error.message)) {
      renderLifecycleErrors.push(error.message);
    }
  });
  await page.addInitScript(() => {
    class MockWebSocket {
      static OPEN = 1;
      readyState = 0;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      constructor() {
        setTimeout(() => {
          this.readyState = 1;
          this.onopen?.();
          this.onmessage?.({ data: JSON.stringify({ type: 'room_members_update', payload: [{ id: 'e2e-user', name: 'E2E 旅行者', role: '寻味探索', intent: '地道美食', avatarSeed: 'e2e-user' }] }) });
        }, 20);
      }
      send(payload: string) {
        const request = JSON.parse(payload);
        if (request.type !== 'agent_negotiate') return;
        setTimeout(() => this.onmessage?.({ data: JSON.stringify({ type: 'target_city', payload: { name: '洛阳', lnglat: [112.45, 34.62] } }) }), 20);
        setTimeout(() => this.onmessage?.({ data: JSON.stringify({ type: 'stream_token', payload: '[FINAL_JSON]{"status":"ok","negotiation_summary":"已完成个性化协商","route":[{"day":1,"location":"洛阳古城","time":"09:00","cost_estimate":"¥80","tags":["地标"]}]}' }) }), 80);
      }
      close() { this.readyState = 3; }
      addEventListener() {}
      removeEventListener() {}
    }
    (window as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;
  });

  await page.route('**/api/v1/**', async route => route.fulfill({ json: { ok: true, success: true, data: {} } }));
  await page.route('**/api/auth/login', async route => route.fulfill({ json: { message: '登录成功', token: user.token, user } }));
  await page.route('**/api/auth/register', async route => route.fulfill({ json: { message: '注册成功', token: user.token, user } }));
  await page.route('**/api/v1/room/create', async route => route.fulfill({ json: { room_id: 'ROOM-E2E', invite_code: 'ROOM-E2E', room_name: 'E2E 旅行房间' } }));
  await page.route('**/api/v1/room/join', async route => route.fulfill({ json: { room_id: 'ROOM-E2E', invite_code: 'ROOM-E2E', room_name: 'E2E 旅行房间' } }));
  await page.route('**/api/v1/budget/**', async route => route.fulfill({ json: { estimated_cost: 180, total: 180, per_day: 180, expenses: [] } }));
  await page.route('**/api/user/**', async route => route.fulfill({ json: { trips: [], posts: [], user } }));
  await page.route('**/api/community/**', async route => route.fulfill({ json: { posts: [], comments: [], tags: [] } }));

  await page.goto('/');
  await page.getByTestId('auth-register-tab').click();
  await page.getByTestId('auth-nickname').fill('E2E 旅行者');
  await page.getByTestId('auth-username').fill('e2e-user');
  await page.getByTestId('auth-password').fill('password123');
  await page.getByTestId('auth-submit').click();
  await expect(page.getByText('注册成功，请使用新账号登录')).toBeVisible();
  await page.getByTestId('auth-username').fill('e2e-user');
  await page.getByTestId('auth-password').fill('password123');
  await page.getByTestId('auth-submit').click();
  await expect(page.getByText('开启下一段旅程协同推演')).toBeVisible();

  // The product-level views must remain usable on a narrow phone, not merely
  // render at desktop width and hide overflow.
  await page.getByRole('button', { name: /个人主页/ }).click();
  await expect(page.getByText('我的路书', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
  await page.getByText('返回大厅', { exact: true }).click();
  await page.getByRole('button', { name: /社区/ }).click();
  await expect(page.getByText('同行者社区', { exact: true })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByText('返回大厅', { exact: true }).click();
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.getByText('打开交互式地图', { exact: true }).click();
  const destinationMap = page.locator('.destination-map-shell');
  await expect(destinationMap).toBeVisible();
  await expect(destinationMap.locator('canvas').first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);
  await page.getByRole('button', { name: '关闭目的地图谱' }).click();
  await page.setViewportSize({ width: 1280, height: 720 });

  await page.getByTestId('join-room-code').fill('ROOM-E2E');
  await page.getByTestId('join-room-submit').click();
  await expect(page.getByTestId('room-code')).toHaveText('ROOM-E2E');
  await page.getByTestId('begin-plan').click();
  await page.getByText('进入共识沙盘').click();
  await expect(page.getByText('你的个性化旅行诉求')).toBeVisible();
  await page.locator('textarea').fill('去洛阳玩1天，想吃地道老字号，节奏不要太赶');
  await page.getByText('锁定意图并开始推演').click();
  await expect(page.getByText('路线共识解释')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('如果这样调整，会发生什么？')).toBeVisible();
  await expect(page.getByText(/预估花销/).first()).toBeVisible();
  const closeBlackboard = page.getByTestId('close-blackboard');
  if (await closeBlackboard.count()) await closeBlackboard.click({ force: true });
  await page.getByRole('button', { name: '卫星图' }).click();
  await expect(page.getByRole('button', { name: '标准图' })).toBeVisible();
  const mapCanvas = page.locator('.map-visualizer-shell canvas').first();
  await expect(mapCanvas).toBeVisible();
  expect((await mapCanvas.screenshot()).byteLength).toBeGreaterThan(2_000);
  await page.getByRole('button', { name: '标准图' }).click();

  await page.getByRole('button', { name: '预算账本' }).click();
  await expect(page.getByRole('dialog', { name: '协作预算账本' })).toBeVisible();
  await page.getByRole('button', { name: '关闭预算账本' }).click();

  await page.getByRole('button', { name: '3D 态势雷达' }).click();
  await expect(page.getByRole('dialog', { name: '3D 态势感知雷达' })).toBeVisible();
  const globeCanvas = page.getByRole('dialog', { name: '3D 态势感知雷达' }).locator('canvas').first();
  await expect(globeCanvas).toBeVisible();
  const desktopBox = await globeCanvas.boundingBox();
  expect(desktopBox?.width).toBeGreaterThan(300);
  expect(desktopBox?.height).toBeGreaterThan(300);
  expect((await globeCanvas.screenshot()).byteLength).toBeGreaterThan(2_000);
  const globalIntel = page.getByRole('button', { name: '打开全球情报视图' });
  await globalIntel.click();
  await expect(page.getByRole('button', { name: '退出全球情报视图' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText(/10 区域 · 20 城市/)).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileBox = await globeCanvas.boundingBox();
  expect(mobileBox?.width).toBeGreaterThan(300);
  expect(mobileBox?.height).toBeGreaterThan(300);
  expect((await globeCanvas.screenshot()).byteLength).toBeGreaterThan(2_000);
  await expectNoHorizontalOverflow(page);
  await page.getByRole('button', { name: '退出雷达' }).click();
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByTestId('vote-up-0').click();
  await page.getByTestId('save-trip').click();
  await expect(page.getByText('已保存')).toBeVisible();
  const jsonDownload = page.waitForEvent('download');
  // 导出 JSON 收在「更多」菜单里（工具栏折叠）
  await page.getByTestId('toggle-more-tools').click();
  await page.getByTestId('export-json').click();
  await expect((await jsonDownload).suggestedFilename()).toContain('.json');
  const icsDownload = page.waitForEvent('download');
  await page.getByTestId('export-ics').click();
  await expect((await icsDownload).suggestedFilename()).toContain('.ics');
  await page.getByTestId('open-diary').click();
  const diaryDownload = page.waitForEvent('download');
  await page.getByTestId('download-diary').click();
  await expect((await diaryDownload).suggestedFilename()).toContain('.md');
  await page.getByText('带入下一轮规划').click();
  await expect(page.getByText('追加诉求并迭代路书')).toBeVisible();
  expect(renderLifecycleErrors).toEqual([]);
});
