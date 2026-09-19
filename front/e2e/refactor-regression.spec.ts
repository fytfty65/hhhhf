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

  test('底图不可达时：离线示意图兜底、提示不被遮挡（版面回归）', async ({ page }) => {
    test.setTimeout(90_000);
    // 让高德瓦片一定失败 → 必定走"底图不可达"这条路径（确定性，不依赖外网快慢）
    await page.route('**/appmaptile**', (route) => route.abort());
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    const status = page.locator('.map-tile-status');
    await expect(status).toBeVisible({ timeout: 30_000 });
    const green = page.getByTitle('碳足迹与绿色交通评估');
    await expect(green).toBeVisible();

    const statusBox = await status.boundingBox();
    const greenBox = await green.boundingBox();
    expect(statusBox).not.toBeNull();
    expect(greenBox).not.toBeNull();
    // 两个浮动元素曾经都贴在左下角（状态条 bottom-20 / 按钮 bottom-[5.25rem]）而重叠，
    // 按钮把状态文字压掉一半。这条断言直接比较两个盒子有没有相交。
    const overlaps =
      statusBox!.x < greenBox!.x + greenBox!.width &&
      greenBox!.x < statusBox!.x + statusBox!.width &&
      statusBox!.y < greenBox!.y + greenBox!.height &&
      greenBox!.y < statusBox!.y + statusBox!.height;
    expect(
      overlaps,
      `状态条与绿色出行按钮重叠：status=${JSON.stringify(statusBox)} green=${JSON.stringify(greenBox)}`,
    ).toBe(false);
    // 而且状态条必须**在按钮上方**（不是被挤到别处去了）
    expect(
      statusBox!.y + statusBox!.height,
      `状态条应位于按钮上方：status=${JSON.stringify(statusBox)} green=${JSON.stringify(greenBox)}`,
    ).toBeLessThanOrEqual(greenBox!.y + 1);

    // 最关键的一条：状态条必须**真的露在最上面**。左下角时它被行程节点卡片条盖住，
    // Playwright 的 toBeVisible 看不出来（元素有盒子、未被 visibility:hidden），
    // 只有 elementFromPoint 能证明"用户读得到"。
    const covered = await page.evaluate(() => {
      const el = document.querySelector('.map-tile-status') as HTMLElement | null;
      if (!el) return { found: false, covered: true };
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) as HTMLElement | null;
      return { found: true, covered: !(el === top || el.contains(top)) };
    });
    expect(covered.found, '状态条应当存在').toBe(true);
    expect(covered.covered, '状态条被其它元素盖住了，用户读不到').toBe(false);

    // 反过来也不能让状态条压住地图控制条（第一次挪到顶部居中时正好压在"实时路况"上）
    const trafficButton = page.getByRole('button', { name: '实时路况' });
    const trafficBox = await trafficButton.boundingBox();
    if (trafficBox) {
      const hitsControl =
        statusBox!.x < trafficBox.x + trafficBox.width &&
        trafficBox.x < statusBox!.x + statusBox!.width &&
        statusBox!.y < trafficBox.y + trafficBox.height &&
        trafficBox.y < statusBox!.y + statusBox!.height;
      expect(
        hitsControl,
        `状态条压住了「实时路况」按钮：status=${JSON.stringify(statusBox)} traffic=${JSON.stringify(trafficBox)}`,
      ).toBe(false);
    }

    // 底图一块都没画出来时，离线示意图必须兜底（不依赖网络），路线与编号仍然可读
    const schematic = page.getByTestId('route-schematic');
    await expect(schematic).toBeVisible({ timeout: 15_000 });
    await expect(schematic.getByText('路线相对位置示意图')).toBeVisible();
    await expect(schematic.getByText(/底图暂时取不到/)).toBeVisible();
    await expect(schematic.getByText(/1\. 洛阳古城/)).toBeVisible();

    await page.screenshot({ path: '../work/map-fallback.png', animations: 'disabled' });
  });
});
