import { test, expect, Page } from '@playwright/test';

/**
 * 房间内节点投票的 UI 回归。
 *
 * 背景：节点上的赞/踩以前只改本机状态，同房间的第二个人完全看不到，
 * 「多人协同投票」只是演示口号。现在前端把投票事实发给房间 socket，
 * 网关广播给其他成员，界面在节点卡片上显示「队友赞/踩」票数。
 *
 * 这个用例用 WebSocket mock 精确验证三点：
 *  1) 收到其他成员的 node_vote 后，节点卡片出现 peer-votes 票数；
 *  2) 自己点赞时，前端真的发出了 {type:'node_vote', node_name, vote:'up'}；
 *  3) 再点一次撤销时发送的 vote 是 null，而不是凭空造一张 down 票。
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

const ROUTE_NODE = '钟楼酒店';

async function installVoteMocks(page: Page) {
  await page.addInitScript(
    (payload) => {
      localStorage.setItem('omni_user', JSON.stringify(payload.user));
      localStorage.setItem(
        'omni_room_' + payload.user.id,
        JSON.stringify({ room_id: 'ROOM-E2E', invite_code: 'ROOM-E2E' }),
      );

      type MockSocket = {
        readyState: number;
        onopen: (() => void) | null;
        onmessage: ((event: { data: string }) => void) | null;
        send: (raw: string) => void;
      };

      class MockWebSocket {
        static OPEN = 1;
        static CLOSED = 3;
        static instances: MockWebSocket[] = [];
        readyState = 0;
        onopen: (() => void) | null = null;
        onclose: (() => void) | null = null;
        onmessage: ((event: { data: string }) => void) | null = null;
        onerror: (() => void) | null = null;

        constructor() {
          MockWebSocket.instances.push(this);
          setTimeout(() => {
            this.readyState = 1;
            this.onopen?.();
            // 房间里除了当前用户，还有一位同行者；成员列表里必须有他，
            // 否则前端清理离线成员票时会把他的票一起删掉。
            this.onmessage?.({
              data: JSON.stringify({
                type: 'room_members_update',
                payload: [
                  { id: 'e2e-user', name: 'E2E 旅行者', role: '寻味探索', intent: '地道美食', avatarSeed: 'e2e-user' },
                  { id: 'peer-user', name: '同行者', role: '文化打卡', intent: '多看博物馆', avatarSeed: 'peer-user' },
                ],
              }),
            });
          }, 20);
        }

        send(raw: string) {
          const request = JSON.parse(raw);
          const w = window as unknown as { __wsSends?: unknown[] };
          w.__wsSends = [...(w.__wsSends || []), request];
          if (request.type !== 'agent_negotiate') return;

          const finish = () => {
            this.onmessage?.({
              data: JSON.stringify({
                type: 'stream_token',
                payload:
                  '[FINAL_JSON]' +
                  JSON.stringify({
                    status: 'ok',
                    negotiation_summary: '已完成个性化协商',
                    route: [
                      {
                        day: 1,
                        location: payload.routeNode,
                        name: payload.routeNode,
                        time: '09:00',
                        cost_estimate: '¥60',
                        tags: ['地标'],
                      },
                    ],
                  }),
              }),
            });

            // 模拟网关把另一位成员的投票广播给本客户端。
            // 必须落在「房间 socket」上：生产环境两个连接都会收到，但只有常驻的
            // 房间 socket 负责聚合投票；推理 socket 只处理推演流。
            const roomSocket = MockWebSocket.instances.find((socket) => socket !== this) as unknown as MockSocket;
            setTimeout(() => {
              roomSocket?.onmessage?.({
                data: JSON.stringify({
                  type: 'node_vote',
                  payload: { node_name: payload.routeNode, vote: 'up', user_id: 'peer-user', nickname: '同行者' },
                }),
              });
            }, 150);
          };

          setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({ type: 'target_city', payload: { name: '西安', lnglat: [108.94, 34.26] } }),
            });
          }, 20);
          setTimeout(finish, 80);
        }

        close() {
          this.readyState = 3;
        }
        addEventListener() {}
        removeEventListener() {}
      }

      (window as unknown as { WebSocket: typeof MockWebSocket }).WebSocket = MockWebSocket;
    },
    { user, routeNode: ROUTE_NODE },
  );
}

test('房间内节点投票：收到队友票、自己的赞与撤销都如实发出去', async ({ page }) => {
  test.setTimeout(120_000);
  await installVoteMocks(page);

  // 只 mock 必须的后端接口；其余接口给一个成功信封，避免真实网络请求。
  await page.route('**/api/v1/**', async (route) => route.fulfill({ json: { ok: true, success: true, data: {} } }));
  await page.route('**/api/user/**', async (route) => route.fulfill({ json: { trips: [], posts: [], user } }));
  await page.route('**/api/community/**', async (route) => route.fulfill({ json: { posts: [], comments: [], tags: [] } }));
  // 实时连接改用一次性票据；localStorage 里的旧令牌会被前端迁移到内存。
  await page.route('**/api/auth/ws-ticket', async (route) => route.fulfill({ json: { ticket: 'e2e-ws-ticket', expires_in: 60 } }));

  // 用已登录 localStorage 直接进入大厅，跳过注册/登录（本用例只关心投票同步）。
  await page.goto('/');
  await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });

  await page.getByTestId('join-room-code').fill('ROOM-E2E');
  await page.getByTestId('join-room-submit').click();
  await expect(page.getByTestId('room-code')).not.toBeEmpty();

  await page.getByTestId('begin-plan').click();
  await expect(page.getByText('查看规划方案')).toBeVisible({ timeout: 10_000 });
  await page.getByText('查看规划方案').click();
  await expect(page.getByText('你的个性化旅行诉求')).toBeVisible({ timeout: 20_000 });

  await page.getByTestId('intent-input').fill('去西安 1 天，想看看钟楼');
  await page.getByText('生成行程方案').click();
  await expect(page.getByText('路线共识解释')).toBeVisible({ timeout: 20_000 });

  // 1) 队友的票经房间 socket 聚合后，节点卡片上出现票数徽标。
  const peerBadge = page.getByTestId('peer-votes-0');
  await expect(peerBadge).toBeVisible({ timeout: 20_000 });
  expect(peerBadge).toContainText('1');
  // 徽标里只有队友的 1 张赞票，当前用户自己还没投票 → 不能把自己算进去变成 2。
  const badgeText = await peerBadge.evaluate((el) => (el.textContent || '').replace(/\s+/g, ''));
  expect(badgeText).toBe('10');

  // 2) 自己点赞：前端必须发出 node_vote，且节点名与票值准确。
  await page.getByTestId('vote-up-0').click();
  await expect
    .poll(
      async () => page.evaluate(() => ((window as any).__wsSends || []).filter((m: any) => m.type === 'node_vote').length),
      { timeout: 5_000 },
    )
    .toBe(1);
  const votesAfterUp = await page.evaluate(() =>
    ((window as any).__wsSends || []).filter((m: any) => m.type === 'node_vote'),
  );
  expect(votesAfterUp[0]?.payload).toEqual({ node_name: ROUTE_NODE, vote: 'up' });
  // 自己的票不能混进「队友票数」里。
  await expect(peerBadge).toHaveText(/1/);

  // 3) 再点一次 = 撤销：发出去的 vote 必须是 null，而不是伪装成 down。
  await page.getByTestId('vote-up-0').click();
  await expect
    .poll(
      async () => page.evaluate(() => ((window as any).__wsSends || []).filter((m: any) => m.type === 'node_vote').length),
      { timeout: 5_000 },
    )
    .toBe(2);
  const votesAfterUndo = await page.evaluate(() =>
    ((window as any).__wsSends || []).filter((m: any) => m.type === 'node_vote'),
  );
  expect(votesAfterUndo[1]?.payload).toEqual({ node_name: ROUTE_NODE, vote: null });
});
