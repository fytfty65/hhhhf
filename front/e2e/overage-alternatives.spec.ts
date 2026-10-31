import { test, expect, Page } from '@playwright/test';

/**
 * 超支替代方案：预算不够时不只报缺口，而是给出可执行的替代选项；
 * 重排回来后按后端 budget_report 如实核对"是否真的压进预算"。
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

const BASE_ROUTE = [
  { day: 1, name: '陕西历史博物馆', type: '博物馆', tags: ['文化'], transport: '地铁', cost_estimate: '¥120' },
  { day: 1, name: '回民街小吃', type: '餐饮', tags: ['美食'], transport: '步行', cost_estimate: '¥60' },
  { day: 1, name: '钟楼酒店', type: '住宿', tags: ['住宿'], is_hotel: true, transport: '打车', cost_estimate: 400 },
  { day: 2, name: '城墙南门', type: '文化', tags: ['地标'], transport: '地铁', cost_estimate: '¥54' },
  { day: 2, name: '书院门面馆', type: '餐饮', tags: ['美食'], transport: '步行', cost_estimate: '¥50' },
];

// 换成更便宜的同类：博物馆降价，住宿与其它安排不动
const CHEAPER_ROUTE = BASE_ROUTE.map((node) =>
  node.name === '陕西历史博物馆' ? { ...node, name: '碑林博物馆', cost_estimate: '¥60' } : node,
);

// 两项合并落实：住宿换成 ¥300 的民宿，博物馆换成 ¥60 的碑林，其余不动
const COMBINED_ROUTE = BASE_ROUTE.map((node) => {
  if (node.is_hotel) return { ...node, name: '巷往小院民宿', cost_estimate: '¥300' };
  if (node.name === '陕西历史博物馆') return { ...node, name: '碑林博物馆', cost_estimate: '¥60' };
  return node;
});

// 初始：超出预算 ¥200
const OVER_BUDGET = { status: 'at_risk', budget: 1200, verified_cost: 1400, estimated_cost: 0, shortfall: 200, confidence: 'medium' };
// 换便宜同类后：压进预算
const WITHIN_BUDGET = { status: 'ok', budget: 1200, verified_cost: 1000, estimated_cost: 50, shortfall: 0, confidence: 'medium' };
// 按预算重排后：仍差 ¥100
const STILL_OVER = { status: 'at_risk', budget: 1200, verified_cost: 1300, estimated_cost: 0, shortfall: 100, confidence: 'medium' };
// 预算改到 1500：花费没变，缺口归零
const RAISED_BUDGET = { status: 'ok', budget: 1500, verified_cost: 1400, estimated_cost: 0, shortfall: 0, confidence: 'medium' };
// 预算改到 900：缺口从 200 扩大到 500
const LOWERED_BUDGET = { status: 'at_risk', budget: 900, verified_cost: 1400, estimated_cost: 0, shortfall: 500, confidence: 'medium' };
// 两项都换掉后：节点合计降到 ¥524（省 ¥160），预算报告与之对齐
const COMBINED_WITHIN = { status: 'ok', budget: 1200, verified_cost: 900, estimated_cost: 50, shortfall: 0, confidence: 'medium' };

async function installMocks(page: Page, options: { combine?: 'both' | 'partial' } = {}) {
  await page.addInitScript((payload) => {
    localStorage.setItem('omni_user', JSON.stringify(payload.user));
    localStorage.setItem('omni_room_' + payload.user.id, JSON.stringify({ room_id: 'ROOM-E2E', invite_code: 'ROOM-E2E' }));
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
              payload: [{ id: 'e2e-user', name: 'E2E 旅行者', role: '寻味探索', intent: '地道美食', avatarSeed: 'e2e-user' }],
            }),
          });
        }, 20);
      }
      send(raw: string) {
        const request = JSON.parse(raw);
        if (request.type !== 'agent_negotiate') return;
        const intent = String(request.payload?.user_preferences?.intent || '');
        // 注意顺序："按预算重排"的指令里也包含"换成更便宜的同类"，必须先判预算再判单点替换；
        // "把总预算改成 X"又要优先于前两者，否则会被当成普通重排。
        // 合并指令里同样带着"换成更便宜的同类"，所以"同时调整"要排在单点替换前面。
        const budgetChange = intent.includes('把总预算改成');
        const combined = intent.includes('同时调整');
        // 一键补做只剩住宿一项时发的是它自己的单步指令，"更实惠"与单点替换的"更便宜"不同。
        const retryLodging = intent.includes('住宿换成更实惠的同类');
        const variant = budgetChange
          ? (/1[.,]500/.test(intent) ? 'raise' : 'lower')
          : combined
            ? (payload.combine === 'partial' ? 'combinedPartial' : 'combined')
            : retryLodging
              ? 'retryLodging'
              : intent.includes('总花费请压到') ? 'budget'
                : intent.includes('换成更便宜的同类') ? 'cheaper'
                  : 'base';
        const route = variant === 'cheaper' || variant === 'combinedPartial'
          ? payload.cheaper
          : variant === 'combined' || variant === 'retryLodging'
            ? payload.combined
            : payload.base;
        const budget = variant === 'cheaper' || variant === 'combinedPartial'
          ? payload.within
          : variant === 'combined' || variant === 'retryLodging'
            ? payload.combinedWithin
            : variant === 'budget'
              ? payload.stillOver
              : variant === 'raise'
                ? payload.raised
                : variant === 'lower'
                  ? payload.lowered
                  : payload.over;
        // 只下发 final_route（权威 payload），并给重排留足时间：
        // 若响应太快，点击后的"正在重排"提示会闪一下就没了，断言抓不到；
        // 全量并行跑时机器更忙，6s 才够稳定抓住这个瞬时提示。
        const delay = variant === 'base' ? 120 : 6_000;
        setTimeout(() => {
          this.onmessage?.({ data: JSON.stringify({ type: 'target_city', payload: { name: '西安', lnglat: [108.94, 34.26] } }) });
        }, 20);
        setTimeout(() => {
          this.onmessage?.({
            data: JSON.stringify({
              type: 'final_route',
              payload: { status: 'ok', route, budget_report: budget, negotiation_summary: '已按预算与偏好完成协商' },
            }),
          });
        }, delay);
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
    base: BASE_ROUTE,
    cheaper: CHEAPER_ROUTE,
    combined: COMBINED_ROUTE,
    over: OVER_BUDGET,
    within: WITHIN_BUDGET,
    combinedWithin: COMBINED_WITHIN,
    stillOver: STILL_OVER,
    raised: RAISED_BUDGET,
    lowered: LOWERED_BUDGET,
    combine: options.combine || 'both',
  });

  await page.route('**/api/v1/**', (route) => route.fulfill({ json: { ok: true, success: true, data: {} } }));
  await page.route('**/api/auth/login', (route) => route.fulfill({ json: { message: '登录成功', token: user.token, user } }));
  await page.route('**/api/auth/ws-ticket', (route) => route.fulfill({ json: { ticket: 'e2e-ws-ticket', expires_in: 60 } }));
  await page.route('**/api/user/**', (route) => route.fulfill({ json: { ok: true, user, trips: [] } }));
  await page.route('**/api/community/**', (route) => route.fulfill({ json: { ok: true, posts: [], tags: [] } }));
}

async function reachDecision(page: Page) {
  await page.getByTestId('join-room-code').fill('ROOM-E2E');
  await page.getByTestId('join-room-submit').click();
  await expect(page.getByTestId('room-code')).not.toBeEmpty();
  await page.getByTestId('begin-plan').click();
  await expect(page.getByText('查看规划方案')).toBeVisible({ timeout: 10_000 });
  await page.getByText('查看规划方案').click();
  await expect(page.getByText('你的个性化旅行诉求')).toBeVisible({ timeout: 20_000 });
  await page.locator('textarea').first().fill('去西安 2 天，想看博物馆和吃小吃');
  await page.getByText('生成行程方案').click();
  await expect(page.getByTestId('workspace-layer-execution')).toBeVisible({ timeout: 20_000 });
}

test.describe('超支替代方案', () => {
  test('超出预算时给出可执行的替代项；换掉最贵的景点后如实确认已压进预算', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('overage-alternatives')).toBeVisible();
    await expect(page.getByTestId('overage-alternatives')).toContainText('当前超出 ¥200（预算 ¥1,200）');
    // 住宿单独成项；报价最高的景点排第一；兜底的"按预算重排"永远在
    await expect(page.getByTestId('overage-option-lodging')).toBeVisible();
    await expect(page.getByTestId('overage-option-node-0')).toContainText('陕西历史博物馆');
    await expect(page.getByTestId('overage-option-budget')).toContainText('按预算 ¥1,200 重排');

    await page.getByTestId('overage-option-node-0').click();
    await expect(page.getByTestId('overage-pending')).toContainText('把「陕西历史博物馆」换成更便宜的同类');

    const result = page.getByTestId('overage-result');
    await expect(result).toBeVisible({ timeout: 20_000 });
    await expect(result).toHaveAttribute('data-tone', 'ok');
    await expect(result).toContainText('不再超支');
    await expect(result).toContainText('¥1,050');
  });

  test('重排后仍然超支时不许说成功，要写清缺口和下一步', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('overage-alternatives')).toBeVisible();
    await page.getByTestId('overage-option-budget').click();
    await expect(page.getByTestId('overage-pending')).toContainText('按预算 ¥1,200 重排');

    const result = page.getByTestId('overage-result');
    await expect(result).toBeVisible({ timeout: 20_000 });
    await expect(result).toHaveAttribute('data-tone', 'warn');
    await expect(result).toContainText('仍超出 ¥100');
    await expect(result).toContainText('可以再换掉一项，或调高预算');
  });

  test('把预算调高到够用：明确说预算已改，替代项不用再执行', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('overage-alternatives')).toBeVisible();
    await page.getByTestId('overage-budget-input').fill('1500');
    await page.getByTestId('overage-budget-apply').click();
    await expect(page.getByTestId('overage-budget-pending')).toContainText('正在按新预算 ¥1,500 重排');

    const verdict = page.getByTestId('overage-budget-result');
    await expect(verdict).toBeVisible({ timeout: 20_000 });
    await expect(verdict).toHaveAttribute('data-tone', 'ok');
    await expect(verdict).toContainText('预算已改为 ¥1,500');
    await expect(verdict).toContainText('不再超支');
    await expect(verdict).toContainText('替代项不用再执行');
    // 缺口归零后不该再劝人去换便宜货：候选项全部收起
    await expect(page.getByTestId('overage-option-lodging')).toHaveCount(0);
    await expect(page.getByTestId('overage-option-node-0')).toHaveCount(0);
  });

  test('把预算调低：如实写新的缺口，并标明单项已不够', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('overage-alternatives')).toBeVisible();
    await page.getByTestId('overage-budget-input').fill('900');
    await page.getByTestId('overage-budget-apply').click();
    await expect(page.getByTestId('overage-budget-pending')).toContainText('正在按新预算 ¥900 重排');

    const verdict = page.getByTestId('overage-budget-result');
    await expect(verdict).toBeVisible({ timeout: 20_000 });
    await expect(verdict).toHaveAttribute('data-tone', 'warn');
    await expect(verdict).toContainText('预算已改为 ¥900');
    await expect(verdict).toContainText('仍超出 ¥500');
    await expect(verdict).toContainText('替代项仍成立');
    // 新的缺口比单项能省的多：要标出"单独换它还不够"
    await expect(page.getByTestId('overage-option-node-0')).toContainText('单独换它还不够');
    await expect(page.getByTestId('overage-option-lodging')).toContainText('还差 ¥100');
  });

  test('勾选两项合并重排：回来后逐项核对落实，并说清合计省了多少、是否压进预算', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('overage-alternatives')).toBeVisible();
    await page.getByTestId('overage-select-lodging').click();
    await page.getByTestId('overage-select-node-0').click();
    const combine = page.getByTestId('overage-combine');
    await expect(combine).toContainText('合并重排这 2 项');
    await combine.click();

    await expect(page.getByTestId('overage-combined-pending')).toContainText(
      '正在合并重排：住宿降一档 + 把「陕西历史博物馆」换成更便宜的同类',
    );

    const result = page.getByTestId('overage-combined-result');
    await expect(result).toBeVisible({ timeout: 20_000 });
    await expect(result).toHaveAttribute('data-tone', 'ok');
    await expect(result).toContainText('住宿：已更换');
    await expect(result).toContainText('「陕西历史博物馆」：已更换');
    await expect(result).toContainText('已计价节点合计省了 ¥160');
    await expect(result).toContainText('不再超支');
  });

  test('合并后有一项没落实：不许说全成了，要逐项点名哪项没换', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page, { combine: 'partial' });
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('overage-alternatives')).toBeVisible();
    await page.getByTestId('overage-select-lodging').click();
    await page.getByTestId('overage-select-node-0').click();
    await page.getByTestId('overage-combine').click();

    const result = page.getByTestId('overage-combined-result');
    await expect(result).toBeVisible({ timeout: 20_000 });
    await expect(result).toHaveAttribute('data-tone', 'warn');
    await expect(result).toContainText('住宿：没有变化，仍住原来那家');
    await expect(result).toContainText('「陕西历史博物馆」：已更换');
    await expect(result).toContainText('已计价节点合计省了 ¥60');
  });

  test('没落实的那项可以一键补做：只重发那一项，已换好的不再动', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page, { combine: 'partial' });
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('overage-alternatives')).toBeVisible();
    await page.getByTestId('overage-select-lodging').click();
    await page.getByTestId('overage-select-node-0').click();
    await page.getByTestId('overage-combine').click();

    const result = page.getByTestId('overage-combined-result');
    await expect(result).toBeVisible({ timeout: 20_000 });
    await expect(result).toHaveAttribute('data-tone', 'warn');
    // 只挑没落实的那一项出来重试，并说清已换好的不再动
    const retry = page.getByTestId('overage-retry-unapplied');
    await expect(retry).toContainText('只补做没落实的 1 项');
    await expect(page.getByText('已经换好的项不会再动')).toBeVisible();
    await retry.click();

    await expect(page.getByTestId('overage-combined-pending')).toContainText('正在补做：住宿降一档');
    await expect(result).toBeVisible({ timeout: 20_000 });
    await expect(result).toHaveAttribute('data-tone', 'ok');
    await expect(result).toContainText('住宿：已更换');
    await expect(result).toContainText('已计价节点合计省了 ¥100');
    await expect(result).toContainText('不再超支');
    // 全部落实后按钮自动收起，不留下一个点不动的入口
    await expect(retry).toHaveCount(0);
  });
});
