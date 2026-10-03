import { test, expect, Page } from '@playwright/test';

/**
 * 旅中风险告警的「最小扰动范围」面板（`RiskPerturbationPanel`）。
 *
 * 背景：gateway 会向房间广播 `risk_subscription_alert`，其中可选带 `minimum_perturbation`
 * （ai-service `/api/v1/risk/replan-scope` 的原样回包）。**此前前端完全没有消费端** ——
 * 整条"旅中风险 → 最小扰动提案 → 用户确认"的链路在界面上是隐形的。
 *
 * 这里用 WebSocket mock 造两种真实形状：
 *  1. 正常提案：给出保留率、受影响节点、动作，并明确"确认前不会改方案"；
 *  2. 服务不可达：只如实说没算出来 + 原因，**绝不**编一份影响范围。
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

/** ai-service /api/v1/risk/replan-scope 成功回包（保留率 75%） */
const OK_PERTURBATION = {
  correlation_id: 'trip-e2e-1',
  status: 'ok',
  proposal: {
    agent_id: 'emergency-agent',
    domain: 'emergency',
    status: 'ok',
    degraded: false,
    candidates: [
      {
        action: 'replace_affected_nodes',
        affected_nodes: ['day2-spot1'],
        preserved_nodes: ['day1-spot1', 'day1-spot2', 'day2-spot2'],
        preserved_ratio: 0.75,
        requires_user_confirmation: true,
        risk: { event: 'risk_changed', severity: 'high' },
      },
    ],
  },
  events: [{ event_type: 'risk.changed', event_id: 'e1' }],
  note: '仅产出提案与事件；用户确认前不写入新版本。',
};

/** gateway 如实标注的失败形状（ai-service 不可达） */
const UNAVAILABLE_PERTURBATION = { status: 'unavailable', reason: 'ai_service_unreachable' };

async function installMocks(page: Page, perturbation: unknown) {
  await page.addInitScript(
    (payload) => {
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
            this.onmessage?.({
              data: JSON.stringify({
                type: 'stream_token',
                payload:
                  '[FINAL_JSON]' +
                  JSON.stringify({
                    status: 'ok',
                    negotiation_summary: '已按预算与偏好完成协商',
                    route,
                    team_satisfaction: { 'E2E 旅行者': 70 },
                  }),
              }),
            });
          }, 80);
          setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({ type: 'final_route', payload: { status: 'ok', route, negotiation_summary: '已按预算与偏好完成协商' } }),
            });
          }, 120);
          // 旅中风险广播：真实形状 { city, snapshot, changes, minimum_perturbation? }
          setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({
                type: 'risk_subscription_alert',
                payload: {
                  city: '西安',
                  changes: [{ type: 'typhoon', severity: 'high', summary: '兵马俑景区临时关闭' }],
                  minimum_perturbation: payload.perturbation,
                },
              }),
            });
          }, 200);
        }
        close() {
          this.readyState = 3;
        }
        addEventListener() {}
        removeEventListener() {}
      }
      (window as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
    },
    { user, perturbation },
  );

  await page.route('**/api/v1/**', (route) => route.fulfill({ json: { ok: true, success: true, data: {} } }));
  await page.route('**/api/auth/login', (route) => route.fulfill({ json: { message: '登录成功', token: user.token, user } }));
  await page.route('**/api/user/**', (route) => route.fulfill({ json: { ok: true, user, trips: [] } }));
  await page.route('**/api/community/**', (route) => route.fulfill({ json: { ok: true, posts: [], tags: [] } }));
}

async function reachExecutionLayer(page: Page) {
  await page.getByTestId('join-room-code').fill('ROOM-E2E');
  await page.getByTestId('join-room-submit').click();
  await expect(page.getByTestId('room-code')).not.toBeEmpty();
  await page.getByTestId('begin-plan').click();
  await expect(page.getByText('进入共识沙盘')).toBeVisible({ timeout: 10_000 });
  await page.getByText('进入共识沙盘').click();
  await expect(page.getByText('你的个性化旅行诉求')).toBeVisible({ timeout: 20_000 });
  await page.locator('textarea').first().fill('去西安 2 天，想看博物馆和吃小吃');
  await page.getByText('生成行程方案').click();
  // 决策阶段才会出现「工作区层级」导航（主方案 / 证据 / 旅中执行）。
  // 这里**刻意不**等主方案层的文案：风险告警到达时会自动把用户带到旅中执行层，
  // 主方案层的内容此时是隐藏的（这正是本用例要验证的行为）。
  await expect(page.getByTestId('workspace-layer-execution')).toBeVisible({ timeout: 20_000 });
  await page.getByTestId('workspace-layer-execution').click();
}

test.describe('旅中风险 · 最小扰动范围', () => {
  test('正常提案：给出保留率 / 受影响节点 / 动作，并明确确认前不改方案', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page, OK_PERTURBATION);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachExecutionLayer(page);

    const panel = page.getByTestId('risk-perturbation');
    await expect(panel).toBeVisible({ timeout: 15_000 });
    await expect(panel.getByRole('heading', { name: '旅中风险' })).toBeVisible();
    await expect(panel.getByText('兵马俑景区临时关闭')).toBeVisible();

    const scope = page.getByTestId('risk-perturbation-scope');
    await expect(scope).toBeVisible();
    await expect(scope.getByText('未受影响节点保留率 75%')).toBeVisible();
    await expect(panel.getByText('最小扰动')).toBeVisible();
    await expect(scope.getByText('1 个节点')).toBeVisible();
    await expect(scope.getByText('day2-spot1')).toBeVisible();
    await expect(scope.getByText('3 个节点')).toBeVisible();
    await expect(scope.getByText(/只在受影响的那几个节点里替换/)).toBeVisible();
    await expect(scope.getByText(/需要你确认后才会改，现在不改/)).toBeVisible();
    // 只说"要确认"不够：必须指出确认入口在哪，否则面板看起来点不动
    await expect(panel.getByText(/确认入口：本层下方的「应急重规划提案」/)).toBeVisible();

    // 面板里不得出现这类"AI 腔"措辞
    const text = (await panel.innerText()).toLowerCase();
    for (const banned of ['ai ', 'ai已', '智能推荐', '一键生成', '✨', '🚀']) {
      expect(text.includes(banned), `面板不应出现「${banned}」`).toBeFalsy();
    }
  });

  test('服务不可达：只说没算出来 + 原因，绝不编影响范围', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page, UNAVAILABLE_PERTURBATION);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachExecutionLayer(page);

    const panel = page.getByTestId('risk-perturbation');
    await expect(panel).toBeVisible({ timeout: 15_000 });
    const box = page.getByTestId('risk-perturbation-unavailable');
    await expect(box).toBeVisible();
    await expect(box.getByText(/这次没算出最小扰动范围/)).toBeVisible();
    await expect(box.getByText(/重规划服务没连上/).first()).toBeVisible();
    await expect(box.getByText(/不会改动你现在的方案/)).toBeVisible();
    // 没算出来就不能有"这次会动/其余保留"的结论块
    await expect(page.getByTestId('risk-perturbation-scope')).toHaveCount(0);
  });
});
