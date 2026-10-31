import { test, expect, Page } from '@playwright/test';

/**
 * 旅中风险推送中心 · 通知可操作化。
 *
 * 背景：早期每条风险提醒只是一段文字，用户看完不知道该做什么——
 * 「知道下雨了」并不等于「知道下一步改哪」。这个用例锁定三类可执行动作：
 *  1. 带重排提案的提醒 → 「查看并确认重排提案」；
 *  2. 没有提案的提醒 → 「按这条提醒重排剩余行程」，且明确"会先回到需求页、不会自动改方案"；
 *  3. 任何提醒 → 「查看受影响的安排」「标记已处理」。
 *
 * 诚实口径：没有受影响节点就不补节点名（这里第一条提醒刻意不带 affected_nodes）。
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
        const route = [
          { day: 1, location: '陕西历史博物馆', name: '陕西历史博物馆', time: '09:00', type: '博物馆', cost_estimate: '¥0', tags: ['文化'] },
          { day: 1, location: '回民街小吃', name: '回民街小吃', time: '12:30', type: '餐饮', cost_estimate: '¥60', tags: ['美食'] },
          { day: 2, location: '城墙南门', name: '城墙南门', time: '09:30', type: '文化', cost_estimate: '¥54', tags: ['地标'] },
        ];
        setTimeout(() => {
          this.onmessage?.({ data: JSON.stringify({ type: 'target_city', payload: { name: '西安', lnglat: [108.94, 34.26] } }) });
        }, 20);
        setTimeout(() => {
          this.onmessage?.({ data: JSON.stringify({ type: 'stream_token', payload: '[FINAL_JSON]' + JSON.stringify({ status: 'ok', negotiation_summary: '已按预算与偏好完成协商', route, team_satisfaction: { 'E2E 旅行者': 70 } }) }) });
        }, 80);
        setTimeout(() => {
          this.onmessage?.({ data: JSON.stringify({ type: 'final_route', payload: { status: 'ok', route, negotiation_summary: '已按预算与偏好完成协商' } }) });
        }, 120);
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
  // 两条提醒：一条带重排提案（有受影响节点），一条只有风险描述（没有节点）。
  await page.route('**/api/v1/risk/notifications', (route) =>
    route.fulfill({
      json: {
        data: {
          unread_count: 3,
          notifications: [
            {
              id: 'notice-replan',
              city: '西安',
              type: 'NEW_ALERT',
              severity: 'HIGH',
              title: '城墙南门临时管制',
              detail: '景区通知南门段临时封闭，原定参观顺序会受影响。',
              source: '目的地景区公告',
              estimated: false,
              status: 'unread',
              created_at: new Date(Date.now() - 60_000).toISOString(),
              observed_at: new Date(Date.now() - 60_000).toISOString(),
              replan_proposal: JSON.stringify({ proposal_id: 'risk-replan-1', status: 'pending_confirmation', affected_nodes: ['城墙南门'] }),
            },
            {
              id: 'notice-plain',
              city: '西安',
              type: 'WEATHER_CHANGE',
              severity: 'HIGH',
              title: '暴雨橙色预警',
              detail: '今晚有强降雨，户外安排可能受影响。',
              source: '国家气象中心',
              estimated: false,
              status: 'unread',
              created_at: new Date(Date.now() - 300_000).toISOString(),
              observed_at: new Date(Date.now() - 300_000).toISOString(),
            },
            {
              id: 'notice-low',
              city: '西安',
              type: 'RISK_LEVEL_CHANGE',
              severity: 'LOW',
              title: '午后短时小雨',
              detail: '下午可能有短时小雨，室内行程基本不受影响。',
              source: '气象辅助信号',
              estimated: true,
              status: 'unread',
              created_at: new Date(Date.now() - 600_000).toISOString(),
              observed_at: new Date(Date.now() - 600_000).toISOString(),
            },
          ],
        },
      },
    }),
  );
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

test.describe('旅中风险通知 · 可执行动作', () => {
  test('提醒给出可执行动作，处理与查看都落到实处', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await page.getByTestId('risk-push-toggle').click();
    const panel = page.getByTestId('risk-push-panel');
    await expect(panel).toBeVisible();

    const replanItem = panel.getByTestId('risk-push-item').filter({ hasText: '城墙南门临时管制' });
    const plainItem = panel.getByTestId('risk-push-item').filter({ hasText: '暴雨橙色预警' });
    await expect(replanItem).toBeVisible();
    await expect(plainItem).toBeVisible();

    // 有提案：直接给确认入口，并露出提案里真实的受影响节点
    await expect(replanItem.getByTestId('risk-action-replan')).toBeVisible();
    await expect(replanItem.getByTestId('risk-severity-label')).toHaveText('高风险');
    await expect(replanItem.getByTestId('risk-next-action')).toContainText('先确认这次重排提案：会调整 1 处，确认前方案不变。');
    await expect(replanItem.getByTestId('risk-scope')).toContainText('影响范围：城墙南门');
    await expect(replanItem.getByTestId('risk-scope')).toContainText('无需处理：其余 2 个安排本次不受影响');
    await expect(replanItem.getByTestId('risk-action-replan-request')).toHaveCount(0);

    // 没提案：不能凭空给节点，只给"按这条提醒重排"并说明不会自动改方案
    await expect(plainItem.getByTestId('risk-action-replan-request')).toBeVisible();
    await expect(plainItem.getByTestId('risk-next-action')).toContainText('暂不用改行程，留意后续更新。');
    await expect(plainItem.getByTestId('risk-scope')).toHaveCount(0);

    // 「查看受影响的安排」把用户带到旅中执行层，而不是只关掉弹窗
    await replanItem.getByTestId('risk-action-view-execution').click();
    await expect(panel).toHaveCount(0);
    await expect(page.getByTestId('execution-scope')).toBeVisible();

    // 回到推送中心：处理掉带提案的那条，动作整体收起，另一条仍可操作
    await page.getByTestId('risk-push-toggle').click();
    await expect(page.getByTestId('risk-push-panel')).toBeVisible();
    const replanItemAgain = page.getByTestId('risk-push-item').filter({ hasText: '城墙南门临时管制' });
    await replanItemAgain.getByTestId('risk-action-ack').click();
    await expect(replanItemAgain).toHaveAttribute('data-status', 'acknowledged');
    await expect(replanItemAgain.getByTestId('risk-push-acknowledged')).toBeVisible();
    await expect(replanItemAgain.getByTestId('risk-action-view-execution')).toHaveCount(0);
    await expect(page.getByTestId('risk-push-item').filter({ hasText: '暴雨橙色预警' }).getByTestId('risk-action-ack')).toBeVisible();

    // 「按这条提醒重排」把风险原文带回需求页，由用户自己确认
    await page.getByTestId('risk-push-item').filter({ hasText: '暴雨橙色预警' }).getByTestId('risk-action-replan-request').click();
    const intent = page.getByTestId('intent-input');
    await expect(intent).toBeEditable();
    await expect(intent).toHaveValue(/暴雨橙色预警/);
    await expect(intent).toHaveValue(/保持未完成节点不变/);
  });

  test('低置信提醒用“建议关注”，并可一键暂不处理且不改行程', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    await page.getByTestId('risk-push-toggle').click();
    const panel = page.getByTestId('risk-push-panel');
    await expect(panel).toBeVisible();
    const lowItem = panel.getByTestId('risk-push-item').filter({ hasText: '午后短时小雨' });
    await expect(lowItem).toBeVisible();

    // 等级不只靠颜色：有文字结论；估算信号措辞温和，不催用户行动
    await expect(lowItem.getByTestId('risk-severity-label')).toHaveText('低风险');
    await expect(lowItem.getByTestId('risk-confidence')).toHaveText('估算信号 · 建议关注');
    await expect(lowItem.getByTestId('risk-next-action')).toContainText('建议关注，暂不用改行程');
    await expect(lowItem.getByTestId('risk-scope')).toHaveCount(0);

    // 一键暂不处理：当天不再提醒，且明确说明行程未改
    await lowItem.getByTestId('risk-action-snooze').click();
    await expect(lowItem).toHaveAttribute('data-status', 'snoozed');
    await expect(lowItem.getByTestId('risk-push-snoozed')).toContainText('今天不再提醒，行程未改动');
    await expect(lowItem.getByTestId('risk-action-ack')).toHaveCount(0);

    // 恢复提醒后操作入口回来，不是一次点击就永久丢掉这条
    await lowItem.getByTestId('risk-action-unsnooze').click();
    await expect(lowItem.getByTestId('risk-push-snoozed')).toHaveCount(0);
    await expect(lowItem.getByTestId('risk-action-snooze')).toBeVisible();
  });
});
