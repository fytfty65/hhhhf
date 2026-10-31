import { test, expect, Page } from '@playwright/test';

// 这些断言覆盖证据层/专业层，需显式切到专业模式（普通模式默认只留主方案与旅中执行）。
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('omni_experience_mode', 'pro'));
});

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
// `cityResolution` 模拟后端 2026-10-08 起下发的目的地解析结果：
//   'matched' 从诉求里认出了城市；'fallback_default' **没认出**、用了兜底城市。
// 留空则完全不带该字段（模拟旧后端），用于验证"没有字段时不显示任何提示"。
async function installMocks(
  page: Page,
  options: { governance?: boolean; cityResolution?: string; budgetStatus?: 'ok' | 'at_risk'; planningSlow?: boolean; loggedOut?: boolean; noTodayExpense?: boolean } = {},
) {
  // 执行状态在 mock 里真实累积：点「标记到访」后再次 GET 能读到，才能验证「今天」随进度推进。
  const executionStates: Record<string, { node_key: string; status: string; delay_minutes?: number; updated_at?: string }> = {};
  await page.addInitScript((payload) => {
    const governanceJson = payload.governance
      ? '"quality":{"score":76.5,"verdict":"pass","gate_passed":true,"gate_failures":[],"unverifiable_count":0,' +
        '"dimensions":{"preference_coverage":1,"pacing":0.9,"space_efficiency":0.9,"truthfulness":0.9,"anchoring":0.9,"diversity":0.9}},' +
        '"budget_report":{"budget":1200,"verified_cost":980,"estimated_cost":120,"unknown_count":1,' +
        `"status":"${payload.budgetStatus}","shortfall":${payload.budgetStatus === 'at_risk' ? 200 : 0},` +
        '"confidence":"medium"},' +
        '"horizon":{"days":2,"segments":[{"start":1,"end":2,"days":2}],"advisories":[]},'
      : '';
    if (!payload.loggedOut) localStorage.setItem('omni_user', JSON.stringify(payload.user));
    // The room bootstrap JSON-parses this value and requires BOTH room_id and
    // invite_code; storing a bare string makes it fall through to a real
    // /api/v1/room/create call, which never resolves here and leaves roomReady
    // false — so the room socket is never created and the workspace never opens.
    if (!payload.loggedOut) localStorage.setItem(
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
      constructor(url?: string) {
        const registry = ((window as unknown as { __mockSockets?: Array<{ socket: MockWebSocket; url: string }> }).__mockSockets ||= []);
        registry.push({ socket: this, url: String(url || '') });
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
        if (payload.planningSlow) {
          // 先吐一条进度日志，1.5 秒后才给最终方案：用于验证等待期的阶段/秒表/停止等待。
          setTimeout(() => {
            this.onmessage?.({ data: JSON.stringify({ type: 'stream_token', payload: '正在核对目的地、开放时间与交通…' }) });
          }, 20);
        }
        setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: 'target_city',
              payload: {
                name: '洛阳',
                lnglat: [112.45, 34.62],
                ...(payload.cityResolution ? { resolution: payload.cityResolution } : {}),
              },
            }),
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
        }, payload.planningSlow ? 1500 : 80);
      }
      close() {
        this.readyState = 3;
      }
      addEventListener() {}
      removeEventListener() {}
    }
    (window as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
  }, {
    user,
    governance: Boolean(options.governance),
    cityResolution: options.cityResolution ?? '',
    budgetStatus: options.budgetStatus ?? 'ok',
    planningSlow: Boolean(options.planningSlow),
    loggedOut: Boolean(options.loggedOut),
  });

  await page.route('**/api/v1/room/create', (route) =>
    route.fulfill({ json: { room_id: 'ROOM-E2E', invite_code: 'ROOM-E2E', room_name: '试用房间' } }),
  );
  await page.route('**/api/v1/**', (route) => {
    const request = route.request();
    const url = request.url();
    if (url.includes('/api/v1/planning/execution')) {
      if (request.method() === 'POST') {
        const body = JSON.parse(request.postData() || '{}');
        const state = {
          node_key: String(body.node_key || ''),
          status: String(body.status || 'planned'),
          delay_minutes: Number(body.delay_minutes) || 0,
          updated_at: new Date().toISOString(),
        };
        executionStates[state.node_key] = state;
        return route.fulfill({ json: { ok: true, success: true, data: { state } } });
      }
      return route.fulfill({ json: { ok: true, success: true, data: { states: Object.values(executionStates) } } });
    }
    if (url.includes('/api/v1/budget/review')) {
      // 复盘「今天优先」：一条今天的记录 + 一条昨天的记录，用来验证两个口径不会混在一起。
      const today = new Date();
      const earlier = new Date(today.getTime() - 36 * 60 * 60 * 1000);
      const expenses = [
        ...(options.noTodayExpense ? [] : [{ id: 'e2e-today', category: '餐饮', amount_cents: 12850, currency: 'CNY', note: '今天午餐', created_at: today.toISOString() }]),
        { id: 'e2e-earlier', category: '门票', amount_cents: 9000, currency: 'CNY', note: '前一天门票', created_at: earlier.toISOString() },
      ];
      const spent = expenses.reduce((sum, item) => sum + item.amount_cents, 0);
      return route.fulfill({ json: {
        ok: true,
        summary: {
          total_budget_cents: 300000,
          spent_cents: spent,
          remaining_cents: 300000 - spent,
          ratio: spent / 300000,
          level: 'ok',
          currency: 'CNY',
          precision: 2,
          by_category_cents: { 餐饮: options.noTodayExpense ? 0 : 12850, 门票: 9000 },
          category_pct: { 餐饮: 58.8, 门票: 41.2 },
          expense_count: expenses.length,
        },
        expenses,
      } });
    }
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
  // 实时连接改用一次性票据；没有这个端点，工作区入口会被（正确地）挡住。
  await page.route('**/api/auth/ws-ticket', (route) =>
    route.fulfill({ json: { ticket: 'e2e-ws-ticket', expires_in: 60 } }),
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
  await expect(page.getByText('查看规划方案')).toBeVisible({ timeout: 10_000 });
  await page.getByText('查看规划方案').click();

  await expect(page.getByText('你的个性化旅行诉求')).toBeVisible({ timeout: 20_000 });
  await page.locator('textarea').first().fill('去洛阳玩1天，想吃地道老字号，节奏不要太赶');
  await page.getByText('生成行程方案').click();
  await expect(page.getByText('路线共识解释')).toBeVisible({ timeout: 20_000 });
}

test.describe('refactor regression', () => {
  test('术语用用户语言：高性价比面板主标题可读，品牌名只留副标题', async ({ page }) => {
    // 评审意见（2026-10）：术语要用户化，但品牌化副标题可以保留。
    // 面板主标题必须回答"这里能帮我做什么"，而不是拿内部代号当标题。
    await installMocks(page);
    await page.goto('/');
    await page.getByText('高性价比', { exact: true }).click();
    await expect(page.getByRole('heading', { name: '预算与性价比' })).toBeVisible();
    // 品牌名仍在，但只作为副标题出现，不再是主标题。
    await expect(page.getByText('极客精算中枢').first()).toBeVisible();
    // 解释文案里不再出现 maximin / 适应度函数这类内部术语。
    await expect(page.getByText(/maximin|适应度函数/)).toHaveCount(0);
  });

  test('目的地是兜底来的就要如实说（不让用户以为是自己要的城市）', async ({ page }) => {
    // 背景（2026-10-08 实测撞到）：诉求里没被认出城市时，后端会**静默**兜底到默认城市。
    // 我那次诉求里写了喀什，最终产出的却是武威行程 —— 用户毫不知情。
    // 后端已下发 `resolution=fallback_default`，前端**必须渲染出来**，
    // 否则等于没做（本项目纪律：没渲染出来的"已做"等于没做）。
    await installMocks(page, { cityResolution: 'fallback_default' });
    await page.goto('/');
    await reachDecision(page);

    const notice = page.getByTestId('city-fallback-notice');
    await expect(notice).toBeVisible();
    // 要说清三件事：没认出、按哪个城市生成、怎么办
    await expect(notice).toContainText('没从您的描述里认出目的地');
    await expect(notice).toContainText('洛阳');
    await expect(notice).toContainText('写明城市');
  });

  test('认出城市时不许出现兜底提示（负向对照）', async ({ page }) => {
    // 只测"会显示"不够 —— 如果提示无条件常驻，用户会被误导成"系统总是没认出"。
    await installMocks(page, { cityResolution: 'matched' });
    await page.goto('/');
    await reachDecision(page);
    await expect(page.getByTestId('city-fallback-notice')).toHaveCount(0);
  });

  test('旧后端不带 resolution 字段时也不显示提示', async ({ page }) => {
    // 兼容性：不能因为字段缺失就误报"没认出目的地"。
    await installMocks(page);
    await page.goto('/');
    await reachDecision(page);
    await expect(page.getByTestId('city-fallback-notice')).toHaveCount(0);
  });

  test('第一次来可以免注册试用，直接进大厅开始规划', async ({ page }) => {
    // 评审意见：新用户不该在登录页被挡住。试用入口必须真的能进门，而不是摆设。
    await installMocks(page, { loggedOut: true });
    await page.goto('/');
    await expect(page.getByTestId('auth-trial')).toBeVisible();
    await expect(page.getByText('先随便逛逛 · 免注册试用')).toBeVisible();

    await page.getByTestId('auth-trial').click();
    // 试用 = 注册随机账号 + 自动登录 + 自动建房，直接落到大厅
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('room-code')).toBeVisible();
  });

  test('lobby keeps every entry point', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');

    // Auth/room surface
    await expect(page.getByText('开启下一段旅程协同规划')).toBeVisible({ timeout: 20_000 });
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
    await expect(page.getByText('查看规划方案')).toBeVisible();
    await noHorizontalOverflow(page);
  });

  test('profile screen reachable and reversible', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByText('开启下一段旅程协同规划')).toBeVisible({ timeout: 20_000 });

    await page.getByRole('button', { name: /个人主页/ }).click();
    await expect(page.getByText('我的路书', { exact: true })).toBeVisible({ timeout: 15_000 });
    await page.setViewportSize({ width: 390, height: 844 });
    await noHorizontalOverflow(page);
    await page.getByText('返回大厅', { exact: true }).click();
    await page.setViewportSize({ width: 1280, height: 720 });
    await expect(page.getByText('开启下一段旅程协同规划')).toBeVisible();
  });

  test('community screen reachable and reversible', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByText('开启下一段旅程协同规划')).toBeVisible({ timeout: 20_000 });

    // 卡片也变成了可访问按钮（aria-label「进入同行者社区」），必须用精确名把导航按钮区分开
    await page.getByRole('button', { name: '社区', exact: true }).click();
    await expect(page.getByText('同行者社区', { exact: true })).toBeVisible({ timeout: 15_000 });
    await page.setViewportSize({ width: 390, height: 844 });
    await noHorizontalOverflow(page);
    await page.getByText('返回大厅', { exact: true }).click();
    await page.setViewportSize({ width: 1280, height: 720 });
    await expect(page.getByText('开启下一段旅程协同规划')).toBeVisible();
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
    // 「方案检查」（专业诊断）层收在「更多」菜单里 —— 先展开菜单再进层
    await page.getByTestId('workspace-layer-more').click();
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
    await page.getByRole('button', { name: '全球风险概览' }).click();
    const radar = page.getByRole('dialog', { name: '全球风险地图' });
    await expect(radar).toBeVisible({ timeout: 15_000 });
    await expect(radar.locator('canvas').first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: '打开全球视野' })).toBeVisible();
    await page.getByRole('button', { name: '关闭全球风险视图' }).click();
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
    // 「方案检查」层收在「更多」菜单里 —— 先展开菜单再进层
    await page.getByTestId('workspace-layer-more').click();
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

    // 方案取舍板（旧称「博弈裁决看板」，重构后拆成三段人话）：当前方案 / 替代方案 / 为什么没选其他方案。
    // 真实可点入口是右上角动作条（visualizer-action-dock）上的「方案取舍」；页面上还有一处同名入口
    // （协作记录区），所以这里按容器限定，避免 strict mode 撞名。
    // 看板里那个旧的「裁决看板」按钮被动作条压住点不到，已作为死按钮从 FullRouteVisualizer 删除。
    await page.locator('.visualizer-action-dock').getByRole('button', { name: '方案取舍' }).click();
    const board = page.getByTestId('plan-arbitration-board');
    await expect(board).toBeVisible({ timeout: 10_000 });
    await expect(board.getByText('当前方案', { exact: true })).toBeVisible();
    await expect(board.getByText('正在使用')).toBeVisible();
    await expect(board.getByText('替代方案', { exact: true })).toBeVisible();
    await expect(board.getByText('为什么没有选其他方案')).toBeVisible();
    // 被放弃的候选必须给出"少了什么"的解释区，而不是只列名字
    await expect(board.getByText(/以下是被放弃的候选/)).toBeVisible();
  });

  test('普通模式首屏只留今日概览，依据与专业信息按需展开', async ({ page }) => {
    // 产品默认是普通模式：首屏只回答今天去哪、总预算、交通耗时、风险提醒，
    // 覆盖率/共识/策略/采样等指标收进「查看依据」，行前清单收进「展开专业信息」。
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);
    // 本文件 beforeEach 会预设 pro，这里显式切回产品默认的普通模式
    await page.getByTestId('experience-mode-simple').click();

    const overview = page.getByTestId('trip-overview');
    await expect(overview).toBeVisible();
    await expect(overview.getByText('今日路线', { exact: true })).toBeVisible();
    await expect(overview.getByText('总预算', { exact: true })).toBeVisible();
    await expect(overview.getByText('交通耗时', { exact: true })).toBeVisible();
    await expect(overview.getByText('风险提醒', { exact: true })).toBeVisible();
    await expect(page.getByLabel('路书质量摘要')).toBeHidden();

    await page.getByTestId('toggle-route-evidence').click();
    await expect(page.getByLabel('路书质量摘要')).toBeVisible();

    await page.getByTestId('toggle-plan-details').click();
    await expect(page.getByText('行前规划工作台')).toBeVisible();
  });

  test('规划中能看见阶段与已用时，停止等待后迟到的方案不再抢屏', async ({ page }) => {
    // 评审意见：等待期要给真实进度感（当前/已完成阶段、已用时），并且能停下来。
    // 后端没有取消通道，所以「停止等待」只保证：立刻回到描述页 + 不接收迟到结果。
    await installMocks(page, { planningSlow: true });
    await page.goto('/');
    await page.getByTestId('join-room-code').fill('ROOM-E2E');
    await page.getByTestId('join-room-submit').click();
    await expect(page.getByTestId('room-code')).not.toBeEmpty();
    await page.getByTestId('begin-plan').click();
    await expect(page.getByText('查看规划方案')).toBeVisible({ timeout: 10_000 });
    await page.getByText('查看规划方案').click();
    await page.locator('textarea').first().fill('去洛阳玩1天，想吃地道老字号，节奏不要太赶');
    await page.getByText('生成行程方案').click();

    await expect(page.getByRole('heading', { name: '正在整理你的行程方案…' })).toBeVisible({ timeout: 10_000 });
    const stages = page.getByTestId('deduction-stages');
    await expect(stages).toContainText('描述需求（已完成）');
    await expect(stages).toContainText('生成方案（进行中）');
    await expect(stages).toContainText('确认行程（待开始）');
    await expect(page.getByTestId('deduction-elapsed')).toHaveText(/\d+ 秒|\d+ 分 \d+ 秒/);

    await page.getByTestId('deduction-cancel').click();
    // 回到诉求描述页（已提交过一次诉求，所以这里显示“继续调整行程/应用调整”）
    await expect(page.getByTestId('deduction-stages')).toHaveCount(0);
    await expect(page.getByText('继续调整行程', { exact: false })).toBeVisible();
    await expect(page.getByText('应用调整')).toBeVisible();
    // 1.5 秒后 mock 才把最终方案发过来：必须被“停止等待”挡在门外
    await page.waitForTimeout(1_800);
    await expect(page.getByText('路线共识解释')).toHaveCount(0);
    await expect(page.getByTestId('trip-overview')).toHaveCount(0);
  });

  test('预算可信度：已确认/待确认/人均/日均都在首屏，金额只认后端预算报告', async ({ page }) => {
    // 评审意见：预算要能看出可信度，而不是只有一个总额。
    // 这里锁定三件事：可信度分级、已确认与待确认分列、人均/日均按真实人数与天数分摊。
    await installMocks(page, { governance: true });
    await page.goto('/');
    await reachDecision(page);
    await page.getByTestId('experience-mode-simple').click();

    const overview = page.getByTestId('trip-overview');
    const credibility = page.getByTestId('budget-credibility');
    await expect(credibility).toBeVisible();
    await expect(page.getByTestId('budget-confidence')).toHaveText('可信度：中');
    await expect(page.getByTestId('budget-verified')).toHaveText('¥980');
    await expect(page.getByTestId('budget-pending')).toHaveText('¥120');
    await expect(credibility.getByText('1 个节点未取价')).toBeVisible();
    // 合计 = 已确认 980 + 待确认 120；人均/日均按 1 人、2 天分摊
    await expect(page.getByTestId('budget-total')).toHaveText('¥1,100');
    await expect(page.getByTestId('budget-per-person')).toHaveText('¥1,100');
    await expect(credibility.getByText('日均 ¥550 · 按 1 人、2 天分摊')).toBeVisible();
    await expect(overview.getByTestId('budget-overrun')).toHaveCount(0);
  });

  test('预算可能超支时首屏要写明缺口，不能只说总额', async ({ page }) => {
    await installMocks(page, { governance: true, budgetStatus: 'at_risk' });
    await page.goto('/');
    await reachDecision(page);
    await page.getByTestId('experience-mode-simple').click();
    await expect(page.getByTestId('budget-overrun')).toHaveText('可能超出预算 ¥200（按已确认+待确认合计）');
  });

  test('键盘：预算弹窗把焦点锁在弹窗内，关闭后还给触发按钮', async ({ page }) => {
    // 焦点锁定（useFocusTrap）是键盘用户唯一能感知的"弹窗边界"：
    // 打开后焦点不进弹窗、Tab 跑到背后的工作台、关闭后焦点掉回 body，
    // 都会让键盘用户在看不见的地方操作页面。这条用例只走真实按键，不用 click。
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    const focusedTitle = () =>
      page.evaluate(() => (document.activeElement as HTMLElement | null)?.title ?? '');
    const focusInDialog = () =>
      page.evaluate(() => document.activeElement?.closest('[role="dialog"]') !== null);

    // 键盘走到「预算账本」：先聚焦它前面的「导出日历」，再按一次 Tab 落上去
    await page.getByTestId('export-ics').focus();
    await page.keyboard.press('Tab');
    await expect.poll(focusedTitle, { message: 'Tab 应该落到「预算账本」按钮上' }).toBe('打开预算管家');
    await page.keyboard.press('Enter');

    const dialog = page.locator('[role="dialog"]').first();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    // 打开后焦点必须已经进入弹窗内部
    await expect.poll(focusInDialog, { message: '打开后焦点应进入弹窗' }).toBe(true);

    // 连按 6 次 Tab：每一步焦点都得留在同一个弹窗里，不能跑到背后的页面
    for (let index = 0; index < 6; index += 1) {
      await page.keyboard.press('Tab');
      await expect
        .poll(focusInDialog, { message: `第 ${index + 1} 次 Tab 后焦点跑出了弹窗` })
        .toBe(true);
    }

    // 关闭按钮是弹窗内第一个可聚焦元素：Shift+Tab 回退到它，再按 Enter 关闭
    const focusedLabel = () => page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? '');
    for (let attempt = 0; attempt < 12; attempt += 1) {
      if ((await focusedLabel()) === '关闭预算账本') break;
      await page.keyboard.press('Shift+Tab');
    }
    await expect.poll(focusedLabel, { message: 'Shift+Tab 应退回关闭按钮' }).toBe('关闭预算账本');
    await page.keyboard.press('Enter');
    await expect(dialog).toBeHidden({ timeout: 10_000 });

    // 关闭后焦点必须还给打开它的那个按钮，而不是掉在 body 上
    await expect.poll(focusedTitle, { message: '关闭后焦点应回到「预算账本」按钮' }).toBe('打开预算管家');
    expect(await page.evaluate(() => document.activeElement?.textContent ?? '')).toContain('预算账本');
  });

  test('旅中执行默认只看今天：走完当天再给「看下一天」，不用明天的地点冒充今天', async ({ page }) => {
    // 评审意见：执行/复盘要「今天优先」。默认范围必须是今天这一天，
    // 今天走完后不能把明天的首个节点当成「接下来」，而是明确给出「看下一天」。
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);
    await page.getByTestId('workspace-layer-execution').click();

    const panel = page.getByLabel('旅中执行模式');
    await expect(panel.getByTestId('execution-scope-today')).toHaveAttribute('aria-pressed', 'true');
    await expect(panel.getByTestId('execution-progress-text')).toHaveText('今天完成 0 / 1 个地点');
    await expect(panel.getByTestId('execution-current')).toContainText('洛阳古城');
    // 今天的列表里不出现第 2 天的地点
    await expect(panel.getByTestId('execution-today-list')).not.toContainText('龙门石窟');

    await panel.getByRole('button', { name: '标记到访' }).click();
    await expect(panel.getByTestId('execution-today-done')).toContainText('今天的安排已完成');
    await expect(panel.getByTestId('execution-progress-text')).toHaveText('今天完成 1 / 1 个地点');
    await expect(panel.getByTestId('execution-current')).toHaveCount(0);

    await panel.getByTestId('execution-next-day').click();
    await expect(panel.getByTestId('execution-current')).toContainText('龙门石窟');

    await panel.getByRole('button', { name: '标记到访' }).click();
    await expect(panel.getByTestId('execution-all-done')).toContainText('本次行程已全部完成');

    await panel.getByTestId('execution-scope-all').click();
    await expect(panel.getByTestId('execution-progress-text')).toHaveText('全部完成 2 / 2 个地点');
  });

  test('复盘默认先看今天：日记只展示当天，切「全部」才看整趟', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await page.getByTestId('open-diary').click();
    await expect(page.getByTestId('diary-scope-today')).toHaveAttribute('aria-pressed', 'true');
    const diary = page.getByTestId('diary-body');
    await expect(diary).toContainText('洛阳古城');
    await expect(diary).not.toContainText('龙门石窟');

    await page.getByTestId('diary-scope-all').click();
    await expect(page.getByTestId('diary-body')).toContainText('龙门石窟');
    await expect(page.getByTestId('diary-body')).toContainText('洛阳古城');
  });

  test('消费复盘默认先看今天：当天明细与合计不和整趟混在一起', async ({ page }) => {
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await page.getByTestId('toggle-more-tools').click();
    await page.getByText('消费复盘').click();

    await expect(page.getByTestId('expense-scope-today')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('expense-today-summary')).toHaveText('今天已记账 1 笔 · 合计 ¥128.50 CNY');
    await expect(page.getByTestId('expense-item')).toHaveCount(1);
    await expect(page.getByText('今天午餐')).toBeVisible();
    await expect(page.getByText('前一天门票')).toHaveCount(0);

    await page.getByTestId('expense-scope-all').click();
    await expect(page.getByTestId('expense-item')).toHaveCount(2);
    await expect(page.getByText('前一天门票')).toBeVisible();
  });

  test('今天没记账就如实说，不拿整趟金额冒充今天', async ({ page }) => {
    await installMocks(page, { noTodayExpense: true });
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await page.getByTestId('toggle-more-tools').click();
    await page.getByText('消费复盘').click();

    await expect(page.getByTestId('expense-today-empty')).toBeVisible();
    await expect(page.getByTestId('expense-today-summary')).toHaveCount(0);
    await expect(page.getByTestId('expense-item')).toHaveCount(0);

    await page.getByTestId('expense-scope-all').click();
    await expect(page.getByTestId('expense-item')).toHaveCount(1);
    await expect(page.getByText('前一天门票')).toBeVisible();
  });

  test('协作断网时如实显示「网络已断开」，恢复后自动重连', async ({ page }) => {
    // 评审意见：协作状态要能看懂、断线要有恢复路径。
    // 以前无论通道是否可用都显示一个绿色「团队实时同步」，这里是负向验证。
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('room-sync')).toHaveAttribute('data-connection', 'connected');
    await expect(page.getByTestId('room-sync-text')).toContainText('协作已连接');
    await expect(page.getByText('团队实时同步')).toHaveCount(0);

    await page.evaluate(() => {
      Object.defineProperty(window.navigator, 'onLine', { configurable: true, get: () => false });
      window.dispatchEvent(new Event('offline'));
    });
    await expect(page.getByTestId('room-sync')).toHaveAttribute('data-connection', 'offline');
    await expect(page.getByTestId('room-offline-banner')).toBeVisible();
    await expect(page.getByTestId('room-banner-text')).toContainText('网络已断开');
    await expect(page.getByTestId('room-banner-retry')).toBeVisible();

    await page.evaluate(() => {
      Object.defineProperty(window.navigator, 'onLine', { configurable: true, get: () => true });
      window.dispatchEvent(new Event('online'));
    });
    await expect(page.getByTestId('room-sync')).toHaveAttribute('data-connection', 'connected');
    await expect(page.getByTestId('room-offline-banner')).toHaveCount(0);
  });
});
