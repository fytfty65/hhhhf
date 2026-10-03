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
// `governance: true` 会额外下发 quality / budget_report 等核对字段 ——
// 证据层的「行程核对说明」面板只有在有核对数据时才渲染（无数据按设计返回 null），
// 所以需要断言该面板的用例必须显式打开这个开关，避免污染其余用例的渲染。
async function installMocks(page: Page, options: { governance?: boolean } = {}) {
  await page.addInitScript((payload) => {
    const governanceJson = payload.governance
      ? '"quality":{"score":76.5,"verdict":"pass","gate_passed":true,"gate_failures":[],"unverifiable_count":0,' +
        '"dimensions":{"preference_coverage":1,"pacing":0.9,"space_efficiency":0.9,"truthfulness":0.9,"anchoring":0.9,"diversity":0.9}},' +
        '"budget_report":{"budget":1200,"verified_cost":980,"estimated_cost":120,"unknown_count":1,"status":"ok","confidence":"medium"},' +
        '"horizon":{"days":2,"segments":[{"start":1,"end":2,"days":2}],"advisories":[]},'
      : '';
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
                '[FINAL_JSON]{"status":"ok",' + governanceJson +
                '"negotiation_summary":"已完成个性化协商",' +
                '"team_satisfaction":{"E2E 旅行者":62},' +
                '"bandit":{"enabled":true,"arm_id":"qwen-plus:relaxed","reason":"thompson_exploitation","explored":false,"propensity":0.71},' +
                '"simulation":{"samples":2000,"total_minutes":{"p50":480,"p90":560,"mean":486},' +
                '"cost":{"known_p50":630,"known_p90":630,"complete_probability":1.0},' +
                '"budget_overrun_probability":0.12,"per_day_minutes_p90":{"1":180},' +
                '"member_satisfaction":{"E2E 旅行者":{"satisfaction_p50":0.62,"satisfaction_floor_p10":0.55}},' +
                '"assumptions":{"nodes":1,"unpriced_nodes":0,"sample_count":2000}},' +
                // 多日行程 + 方案裁决数据：驱动「多日看板」与博弈裁决板的三列对比
                '"optimization_audit":{"method":"Pareto","frontier_count":2,"rejected_count":1,' +
                '"rejected":[{"candidate":"暴走方案","reason":"dominated","dominated_by":["慢游方案"]}]},' +
                '"plan_variants":[' +
                '{"id":"relaxed","name":"慢游方案","style":"relaxed","desc":"少走路、多停留",' +
                '"route":[{"day":1,"location":"龙门石窟","time":"10:00","cost_estimate":"¥90","lnglat":[112.47,34.55],"tags":["文化"]}]},' +
                '{"id":"intense","name":"暴走方案","style":"intense","desc":"一天多看两个点",' +
                '"route":[{"day":2,"location":"白马寺","time":"09:00","cost_estimate":"¥50","lnglat":[112.6,34.72],"tags":["文化"]}]}],' +
                '"route":[{"day":1,"location":"洛阳古城","name":"洛阳古城","time":"09:00","cost_estimate":"¥80","lnglat":[112.45,34.62],"tags":["地标"]},' +
                '{"day":2,"location":"龙门石窟","name":"龙门石窟","time":"09:30","cost_estimate":"¥90","lnglat":[112.47,34.55],"tags":["文化"]}]}',
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
  }, { user, governance: Boolean(options.governance) });

  await page.route('**/api/v1/**', (route) => {
    // Keep the professional diagnostics path deterministic without contacting
    // a live Gateway. This mirrors the bounded quality contract returned by
    // PlanningQualityHandler and exercises the release-gate summary in the UI.
    if (route.request().url().includes('/api/v1/planning/quality')) {
      return route.fulfill({ json: {
        ok: true,
        success: true,
        data: {
          events: 24,
          training_ready: true,
          provider_quality_gate: {
            status: 'available',
            providers: 2,
            ready_providers: 2,
            insufficient_providers: 0,
            blocked_providers: 0,
            reasons: [],
            alerts: [],
          },
          rates: { adoption: 0.75 },
          drift: { status: 'stable' },
          counterfactual: { status: 'available' },
        },
      } });
    }
    return route.fulfill({ json: { ok: true, success: true, data: {} } });
  });
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
  await page.getByText('生成行程方案').click();
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
    await installMocks(page, { governance: true });
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    // The decision workspace exposes four focused layers instead of mounting
    // every specialist panel at once.
    await expect(page.getByTestId('workspace-layer-plan')).toHaveAttribute('aria-pressed', 'true');
    await page.getByTestId('workspace-layer-evidence').click();
    await expect(page.getByLabel('行程核对说明')).toBeVisible();
    await page.getByTestId('workspace-layer-execution').click();
    // 「旅中执行模式」是面板的 aria-label，不是可见标题
    await expect(page.getByLabel('旅中执行模式')).toBeVisible();
    await page.getByTestId('workspace-layer-professional').click();
    await expect(page.getByTestId('professional-diagnostics')).toBeVisible();
    // 供应商质量门禁落在「专业诊断 · 高级诊断」里，默认收起 —— 先展开再断言
    await page.getByTestId('toggle-advanced-diagnostics').click();
    await expect(page.getByTestId('provider-quality-release-gate')).toContainText('供应商质量门禁通过');
    await expect(page.getByTestId('provider-quality-release-gate')).not.toContainText('需处理');
    await page.getByTestId('workspace-layer-plan').click();

    // Toolbar: 常用 4 个在外面，其余收进「更多」（10 个按钮挤成一团太乱）
    await expect(page.getByTestId('save-trip')).toBeVisible();
    await expect(page.getByTestId('open-diary')).toBeVisible();
    await expect(page.getByTestId('export-ics')).toBeVisible();
    await expect(page.getByText('预算账本')).toBeVisible();
    // 折叠项默认不可见
    await expect(page.getByTestId('export-json')).toBeHidden();
    await page.getByTestId('toggle-more-tools').click();
    await expect(page.getByTestId('export-json')).toBeVisible();
    await expect(page.getByText('行程海报')).toBeVisible();
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
    // 点了主行按钮 → 「更多」菜单自动收起
    await expect(page.getByTestId('export-json')).toBeHidden();

    await page.getByTestId('toggle-more-tools').click();
    await page.getByText('行程海报').click();
    await page.keyboard.press('Escape');
    // 菜单点完就收起（不留在屏幕上），主行按钮仍在
    await expect(page.getByTestId('export-json')).toBeHidden();
    await expect(page.getByTestId('save-trip')).toBeVisible();

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
    const radar = page.getByRole('dialog', { name: '全球风险地图' });
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

  test('专业诊断可展开 + 多日看板 + 方案裁决对比', async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    // 高级诊断默认收起；展开后才出现「智能体运行明细」二级折叠
    await page.getByTestId('workspace-layer-professional').click();
    await expect(page.getByTestId('professional-diagnostics')).toBeVisible();
    const advanced = page.getByTestId('toggle-advanced-diagnostics');
    await expect(advanced).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByTestId('toggle-agent-runtime-detail')).toBeHidden();
    await advanced.click();
    await expect(advanced).toHaveAttribute('aria-expanded', 'true');
    const runtime = page.getByTestId('toggle-agent-runtime-detail');
    await expect(runtime).toHaveAttribute('aria-expanded', 'false');
    await runtime.click();
    await expect(runtime).toHaveAttribute('aria-expanded', 'true');

    // 多日看板：切到行程数据看板，两天行程必须都落在时间轴上
    await page.getByTestId('workspace-layer-plan').click();
    await page.getByRole('button', { name: '行程数据看板' }).click();
    await expect(page.getByText('行程总览')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('每日白天节点')).toBeVisible();
    await expect(page.getByText(/D1:\d+ · D2:\d+/).first()).toBeVisible();

    // 博弈裁决板：三列真正对比（保留 / 淘汰+原因 / 替代）
    // 用右上角常驻动作条上的「博弈裁决看板」入口：MACRO 面板内嵌的「裁决看板」按钮
    // 会被该动作条（全局 z-50 覆盖 z-20 面板）遮住，动作条入口才是真实可点路径。
    await page.getByRole('button', { name: '博弈裁决看板' }).click();
    const board = page.getByTestId('plan-arbitration-board');
    await expect(board).toBeVisible({ timeout: 10_000 });
    await expect(board.getByText('保留方案', { exact: true })).toBeVisible();
    await expect(board.getByText('淘汰方案与原因', { exact: true })).toBeVisible();
    await expect(board.getByText('替代方案', { exact: true })).toBeVisible();
    await expect(board.getByText(/被 慢游方案 支配/)).toBeVisible();
    await expect(board.getByText('慢游方案').first()).toBeVisible();
    // 保留方案列必须真的落到具体方案（而不是"当前只有一套方案"空态）
    await expect(board.getByText('AI 主方案')).toBeVisible();
  });
});
