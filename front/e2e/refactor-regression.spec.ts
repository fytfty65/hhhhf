import { test, expect, Page } from '@playwright/test';

/**
 * Refactor safety net.
 *
 * ContextualLobby.tsx holds the whole product in one file (21 top-level
 * components, ~4k lines). Before splitting it up this spec pins the behaviour
 * that must survive: every screen, every workspace phase, and every panel
 * trigger must still mount and expose the same controls.
 *
 * It is deliberately assertion-heavy on *reachability* rather than pixel
 * layout, because the refactor moves declarations between files and must not
 * change what the user can see or click.
 *
 * Run against a dev server:
 *   E2E_BASE_URL=http://localhost:3101 npx playwright test e2e/refactor-regression.spec.ts
 */

const user = {
  id: 'e2e-user',
  username: 'e2e-user',
  nickname: 'E2E 旅行者',
  avatarSeed: 'e2e-user',
  avatarUrl: '',
  signature: '',
  token: 'e2e-token',
  isLoggedIn: true,
};

// Mirrors the mock used by core-flow.spec.ts so the app reaches the decision
// phase without a real gateway.
async function installMocks(page: Page) {
  await page.addInitScript((payload) => {
    localStorage.setItem('omni_user', JSON.stringify(payload.user));
    // The room bootstrap JSON-parses this value and requires BOTH room_id and
    // invite_code; storing a bare string makes it fall through to a real
    // /api/v1/room/create call, which never resolves here and leaves roomReady
    // false — so the room socket is never created and the workspace never opens.
    localStorage.setItem(
      'omni_room_' + payload.user.id,
      JSON.stringify({ room_id: 'ROOM-E2E', invite_code: 'ROOM-E2E' }),
    );
    class MockWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 0;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      constructor() {
        setTimeout(() => {
          this.readyState = 1;
          this.onopen?.();
          this.onmessage?.({
            data: JSON.stringify({
              type: 'room_members_update',
              payload: [
                { id: 'e2e-user', name: 'E2E 旅行者', role: '寻味探索', intent: '地道美食', avatarSeed: 'e2e-user' },
              ],
            }),
          });
        }, 20);
      }
      send(raw: string) {
        const request = JSON.parse(raw);
        if (request.type !== 'agent_negotiate') return;
        setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({ type: 'target_city', payload: { name: '洛阳', lnglat: [112.45, 34.62] } }),
          });
        }, 20);
        setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: 'stream_token',
              payload:
                '[FINAL_JSON]{"status":"ok","negotiation_summary":"已完成个性化协商",' +
                '"team_satisfaction":{"E2E 旅行者":62},' +
                '"bandit":{"enabled":true,"arm_id":"qwen-plus:relaxed","reason":"thompson_exploitation","explored":false,"propensity":0.71},' +
                '"simulation":{"samples":2000,"total_minutes":{"p50":480,"p90":560,"mean":486},' +
                '"cost":{"known_p50":630,"known_p90":630,"complete_probability":1.0},' +
                '"budget_overrun_probability":0.12,"per_day_minutes_p90":{"1":180},' +
                '"member_satisfaction":{"E2E 旅行者":{"satisfaction_p50":0.62,"satisfaction_floor_p10":0.55}},' +
                '"assumptions":{"nodes":1,"unpriced_nodes":0,"sample_count":2000}},' +
                '"route":[{"day":1,"location":"洛阳古城","time":"09:00","cost_estimate":"¥80","lnglat":[112.45,34.62],"tags":["地标"]}]}',
            }),
          });
        }, 80);
      }
      close() {
        this.readyState = 3;
      }
      addEventListener() {}
      removeEventListener() {}
    }
    (window as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
  }, { user });

  await page.route('**/api/v1/**', (route) => route.fulfill({ json: { ok: true, success: true, data: {} } }));
  await page.route('**/api/auth/login', (route) =>
    route.fulfill({ json: { message: '登录成功', token: user.token, user } }),
  );
  await page.route('**/api/auth/register', (route) =>
    route.fulfill({ json: { message: '注册成功', token: user.token, user } }),
  );
  await page.route('**/api/user/**', (route) => route.fulfill({ json: { ok: true, user, trips: [] } }));
  await page.route('**/api/community/**', (route) => route.fulfill({ json: { ok: true, posts: [], tags: [] } }));
}

async function noHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport + 1);
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport + 1);
}

/** Drive the app from the lobby to the decision phase.
 *
 * The room-join step is not optional: the room WebSocket is only created once a
 * room code is known, and workspace entry is gated on that socket being open.
 * Skipping it leaves the app on the lobby with no error shown.
 */
async function reachDecision(page: Page) {
  await page.getByTestId('join-room-code').fill('ROOM-E2E');
  await page.getByTestId('join-room-submit').click();
  // Assert the room code is populated rather than matching exact text: the node
  // renders the code alongside decorative children.
  await expect(page.getByTestId('room-code')).not.toBeEmpty();

  await page.getByTestId('begin-plan').click();
  await expect(page.getByText('进入共识沙盘')).toBeVisible({ timeout: 10_000 });
  await page.getByText('进入共识沙盘').click();

  await expect(page.getByText('你的个性化旅行诉求')).toBeVisible({ timeout: 20_000 });
  await page.locator('textarea').first().fill('去洛阳玩1天，想吃地道老字号，节奏不要太赶');
  await page.getByText('锁定意图并开始推演').click();
  await expect(page.getByText('路线共识解释')).toBeVisible({ timeout: 20_000 });
}

test.describe('refactor regression', () => {
  test('lobby keeps every entry point', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');

    // Auth/room surface
    await expect(page.getByText('开启下一段旅程协同推演')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('room-code')).toBeVisible();
    await expect(page.getByTestId('join-room-code')).toBeVisible();
    await expect(page.getByTestId('join-room-submit')).toBeVisible();
    await expect(page.getByTestId('begin-plan')).toBeVisible();

    // Three travel modes (TravelModeCard)
    await expect(page.getByText('一人行')).toBeVisible();
    await expect(page.getByText('亲友结伴')).toBeVisible();
    await expect(page.getByText('高性价比')).toBeVisible();

    // Preference profile card and lobby entry points
    await expect(page.getByText('我的旅程诉求画像')).toBeVisible();
    await expect(page.getByText('打开交互式地图', { exact: true })).toBeVisible();
    await expect(page.getByText('同行者社区')).toBeVisible();

    await noHorizontalOverflow(page);
  });

  test('preference modal keeps all four roles (PreferenceCard)', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await page.getByTestId('begin-plan').click();

    for (const role of ['寻味探索', '视觉体验', '休闲漫步', '深度探索']) {
      await expect(page.getByText(role, { exact: true }).first()).toBeVisible();
    }
    await expect(page.getByText('进入共识沙盘')).toBeVisible();
    await noHorizontalOverflow(page);
  });

  test('profile screen reachable and reversible', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByText('开启下一段旅程协同推演')).toBeVisible({ timeout: 20_000 });

    await page.getByRole('button', { name: /个人主页/ }).click();
    await expect(page.getByText('我的路书', { exact: true })).toBeVisible({ timeout: 15_000 });
    await page.setViewportSize({ width: 390, height: 844 });
    await noHorizontalOverflow(page);
    await page.getByText('返回大厅', { exact: true }).click();
    await page.setViewportSize({ width: 1280, height: 720 });
    await expect(page.getByText('开启下一段旅程协同推演')).toBeVisible();
  });

  test('community screen reachable and reversible', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByText('开启下一段旅程协同推演')).toBeVisible({ timeout: 20_000 });

    await page.getByRole('button', { name: /社区/ }).click();
    await expect(page.getByText('同行者社区', { exact: true })).toBeVisible({ timeout: 15_000 });
    await page.setViewportSize({ width: 390, height: 844 });
    await noHorizontalOverflow(page);
    await page.getByText('返回大厅', { exact: true }).click();
    await page.setViewportSize({ width: 1280, height: 720 });
    await expect(page.getByText('开启下一段旅程协同推演')).toBeVisible();
  });

  test('destination map opens, renders and closes', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByText('打开交互式地图', { exact: true })).toBeVisible({ timeout: 20_000 });
    await page.getByText('打开交互式地图', { exact: true }).click();

    const shell = page.locator('.destination-map-shell');
    await expect(shell).toBeVisible({ timeout: 15_000 });
    await expect(shell.locator('canvas').first()).toBeVisible({ timeout: 15_000 });
    await page.setViewportSize({ width: 390, height: 844 });
    await noHorizontalOverflow(page);
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.getByRole('button', { name: '关闭目的地图谱' }).click();
    await expect(shell).toBeHidden({ timeout: 10_000 });
  });

  test('workspace decision phase keeps tools, quality strip and panels', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    // Toolbar (all delegate to extracted panels)
    await expect(page.getByTestId('save-trip')).toBeVisible();
    await expect(page.getByTestId('open-diary')).toBeVisible();
    await expect(page.getByTestId('export-ics')).toBeVisible();
    await expect(page.getByTestId('export-json')).toBeVisible();
    await expect(page.getByText('行程海报')).toBeVisible();
    await expect(page.getByText('预算账本')).toBeVisible();
    await expect(page.getByText('消费复盘')).toBeVisible();
    await expect(page.getByText('行程评价')).toBeVisible();
    await expect(page.getByText('官方渠道')).toBeVisible();
    await expect(page.getByText('重新调整')).toBeVisible();

    // Quality strip, including the simulation distribution added earlier
    await expect(page.getByLabel('路书质量摘要')).toBeVisible();
    await expect(page.getByText(/时长 P50/)).toBeVisible();
    await expect(page.getByText(/超预算概率/)).toBeVisible();

    // Panels mount
    await page.getByText('预算账本').click();
    await expect(page.getByRole('dialog', { name: '协作预算账本' })).toBeVisible({ timeout: 10_000 });
    await page.getByRole('button', { name: '关闭预算账本' }).click();

    await page.getByText('行程海报').click();
    await expect(page.getByText('行程海报').first()).toBeVisible();
    await page.keyboard.press('Escape');

    await page.setViewportSize({ width: 390, height: 844 });
    await noHorizontalOverflow(page);
    await page.setViewportSize({ width: 1280, height: 720 });
  });

  test('map visualizer and radar keep working', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    // Map column: layer toggle
    await page.getByRole('button', { name: '卫星图' }).click();
    await expect(page.getByRole('button', { name: '标准图' })).toBeVisible({ timeout: 10_000 });
    const mapCanvas = page.locator('.map-visualizer-shell canvas').first();
    await expect(mapCanvas).toBeVisible({ timeout: 15_000 });
    await page.getByRole('button', { name: '标准图' }).click();

    // Radar opens and exposes the global intel toggle
    await page.getByRole('button', { name: '3D 态势雷达' }).click();
    const radar = page.getByRole('dialog', { name: '3D 态势感知雷达' });
    await expect(radar).toBeVisible({ timeout: 15_000 });
    await expect(radar.locator('canvas').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: '打开全球情报视图' })).toBeVisible();
    await page.getByRole('button', { name: '退出雷达' }).click();
    await expect(radar).toBeHidden({ timeout: 10_000 });
  });

  test('地图瓦片请求确实发出去了（CSP 连接层回归）', async ({ page }) => {
    test.setTimeout(90_000);
    // 2026-09-19 事故：瓦片域名只在 CSP 的 img-src 里、不在 connect-src 里，
    // 而 MapLibre 是用 fetch 取栅格瓦片的 → 浏览器在发请求之前就拦掉，
    // 表现为"地图全白 + 网络面板零请求 + 控制台一堆 Refused to connect"。
    // 这条断言只看"请求有没有真的发出去"：无论外网通不通，请求事件都必须出现。
    const tileRequests: string[] = [];
    page.on('request', (request) => {
      if (/autonavi|arcgisonline|openstreetmap/.test(request.url())) tileRequests.push(request.url());
    });
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect.poll(() => tileRequests.length, { timeout: 25_000 }).toBeGreaterThan(0);
  });
});
