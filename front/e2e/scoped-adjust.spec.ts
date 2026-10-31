import { test, expect, Page } from '@playwright/test';

/**
 * 只调整一部分：只重算某一天 / 只替换交通住宿餐饮。
 *
 * 两条链路必须同时成立才算做完：
 *  ① 点下去要真的发出"范围明确"的增量（界面有等待提示）；
 *  ② 新方案回来要**逐天核对**实际改到了哪里，只改指定范围才说"只改了"，
 *     越界改动必须如实提醒，不能由按钮文案替后端保证。
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
  { day: 1, name: '陕西历史博物馆', type: '博物馆', tags: ['文化'], transport: '地铁', cost_estimate: '¥0' },
  { day: 1, name: '回民街小吃', type: '餐饮', tags: ['美食'], transport: '步行', cost_estimate: '¥60' },
  { day: 1, name: '钟楼酒店', type: '住宿', tags: ['住宿'], is_hotel: true, transport: '打车', cost_estimate: '¥400' },
  { day: 2, name: '城墙南门', type: '文化', tags: ['地标'], transport: '地铁', cost_estimate: '¥54' },
  { day: 2, name: '书院门面馆', type: '餐饮', tags: ['美食'], transport: '步行', cost_estimate: '¥50' },
];

// 只改第 2 天：d1 完全不动
const DAY2_VARIANT = BASE_ROUTE.map((node) => (node.name === '城墙南门' ? { ...node, name: '大唐不夜城' } : node));
// 只换住宿：住宿换了，但景点也被动过 —— 必须报越界
const LODGING_VARIANT = BASE_ROUTE.map((node) => {
  if (node.is_hotel) return { ...node, name: '南门民宿' };
  if (node.name === '陕西历史博物馆') return { ...node, name: '碑林博物馆' };
  return node;
});

async function installMocks(page: Page) {
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
        const variant = intent.includes('只重算第 2 天') ? 'day2' : intent.includes('只替换住宿') ? 'lodging' : 'base';
        const route = variant === 'day2' ? payload.day2 : variant === 'lodging' ? payload.lodging : payload.base;
        // 只下发 final_route（权威 payload）：若再补一条流式 [FINAL_JSON]，
        // 它会比 final_route 更早把路线写进界面，测试点按钮时上一轮结果仍在路上，
        // 导致"等待提示"被上一轮结果提前覆盖 —— 这是实测到的竞态。
        const delay = variant === 'base' ? 120 : 3_000;
        setTimeout(() => {
          this.onmessage?.({ data: JSON.stringify({ type: 'target_city', payload: { name: '西安', lnglat: [108.94, 34.26] } }) });
        }, 20);
        setTimeout(() => {
          this.onmessage?.({ data: JSON.stringify({ type: 'final_route', payload: { status: 'ok', route, negotiation_summary: '已按预算与偏好完成协商' } }) });
        }, delay);
      }
      close() {
        this.readyState = 3;
      }
      addEventListener() {}
      removeEventListener() {}
    }
    (window as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
  }, { user, base: BASE_ROUTE, day2: DAY2_VARIANT, lodging: LODGING_VARIANT });

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

test.describe('只调整一部分', () => {
  test('只重算第 2 天：只改第 2 天时明确说"其它部分没有变"', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('scoped-adjust')).toBeVisible();
    await expect(page.getByTestId('scoped-adjust-day-2')).toBeVisible();
    await expect(page.getByTestId('scoped-adjust-category-transport')).toBeVisible();

    await page.getByTestId('scoped-adjust-day-2').click();
    await expect(page.getByTestId('scoped-adjust-pending')).toBeVisible();
    await expect(page.getByTestId('scoped-adjust-pending')).toContainText('只改第 2 天');

    const report = page.getByTestId('scoped-adjust-report');
    await expect(report).toBeVisible({ timeout: 20_000 });
    await expect(report).toHaveAttribute('data-tone', 'ok');
    await expect(report).toContainText('只改了第 2 天：1 处调整，其它部分没有变。');
  });

  test('只换住宿：景点被顺带改了就要如实提醒，不假装只改了住宿', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await expect(page.getByTestId('scoped-adjust')).toBeVisible();
    await page.getByTestId('scoped-adjust-category-lodging').click();
    await expect(page.getByTestId('scoped-adjust-pending')).toContainText('只改住宿');

    const report = page.getByTestId('scoped-adjust-report');
    await expect(report).toBeVisible({ timeout: 20_000 });
    await expect(report).toHaveAttribute('data-tone', 'warn');
    await expect(report).toContainText('注意：这次除了住宿');
    await expect(report).toContainText('陕西历史博物馆');
    await expect(report).toContainText('没有完全按「只改住宿」执行');
  });
});
