import { test, expect, Page } from '@playwright/test';

/**
 * 行程核对面板（预算核对 / 已作调整 / 行程规模 / 本次调整 / 价格核对 / 跨城怎么走 / 长途分段 /
 * 还需要你留意）的渲染与视觉回归。
 *
 * 数据来自后端 final_route.payload 的 quality / budget_report / fallback / horizon /
 * increment / price_audit / transport_audit / long_trip；
 * 这里用 WebSocket mock 造出一份"预算可能超支 + 已给平替 + 超长行程 + 有遗留问题 + 二次增量已执行
 * + 价格补全 + 跨城比较 + 分段修补"的真实形状 payload，既做断言（文案必须出现），也存一张截图供
 * 人工审阅排版。
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

// 已保存的行程请求体：用来验证 ai-service 的 correlation_id 真的随行程落库
// （不落库的话，网关侧的采纳/到访事件只能退回 trip_id，跨服务轨迹就串不起来）
let savedTripBodies: any[] = [];
// 采纳上报（user.accepted）与方案落库：用来验证"自动保存 ≠ 采纳"这条口径
let adoptCalls: any[] = [];
let variantSaves: any[] = [];

// 与后端 plan_quality_snapshot / budget_report / propose_fallback 输出的字段保持一致
const GOVERNANCE_PAYLOAD = {
  quality: {
    score: 76.5,
    verdict: 'fail',
    gate_passed: false,
    gate_failures: [
      { code: 'skeleton_play_missing', detail: '第 3 天没有游玩安排' },
      { code: 'open_time_missing', detail: '「陕西历史博物馆」营业时间未核实' },
    ],
    unverifiable_count: 3,
    dimensions: { preference_coverage: 1, pacing: 0.8, space_efficiency: 0.9, truthfulness: 0.6, anchoring: 0.85, diversity: 0.95 },
  },
  budget_report: {
    budget: 1200,
    verified_cost: 1164,
    estimated_cost: 260,
    unknown_count: 2,
    status: 'at_risk',
    shortfall: 224,
    confidence: 'medium',
  },
  fallback: {
    trigger: 'budget_shortfall',
    shortfall: 224,
    remaining_shortfall: 0,
    substitutions: [
      { from: '钟楼酒店', to: '青旅床位', saving: 480, reason: '把「钟楼酒店」换成同类的「青旅床位」' },
      { from: '豪华博物馆', to: '社区博物馆（免费）', saving: 300, reason: '把「豪华博物馆」换成同类的「社区博物馆（免费）」' },
    ],
    dropped: [],
    preserved_ratio: 1,
    needs_confirmation: false,
    disclosure: '你的预算比「已核实花费」少约 ¥224；我做了 2 处平替（合计约省 ¥780）；原始偏好保留率约 100%；预算覆盖度约 100%',
  },
  horizon: {
    days: 16,
    segments: [
      { start: 1, end: 7, days: 7 },
      { start: 8, end: 14, days: 7 },
      { start: 15, end: 16, days: 2 },
    ],
    advisories: [
      '行程 16 天属超长行程：建议按 7 天分段生成与校验（共 3 段），否则单次生成节点过多、可靠性下降',
      '住宿建议按「同一城市连续住」聚合，避免逐夜重复下单与比价成本',
      '长行程必须设置中途休整日（建议每 7 天至少 1 天低强度）',
    ],
  },
  // 二次增量：这次新说的话到底落实了几项、对其它安排扰动多大（core/increment.measure_increment）
  increment: {
    requested: 2,
    achieved: 2,
    target_gain: 1,
    disturbance: 0.286,
    responsiveness: 0.714,
    disclosure: '这次你要的 2 项调整都已落实（多吃地道美食 2 处、不去城墙南门），其余安排保留约 71%。',
  },
  // 价格核对：可核实比例 / 未取到比例 / 还需补价的节点（core/price_sources.price_coverage）
  price_audit: {
    total: 5,
    verified: ['钟楼酒店', '陕西历史博物馆'],
    estimated: ['回民街小吃'],
    unknown: ['城墙南门门票', '永兴坊美食'],
    verified_ratio: 0.4,
    unknown_ratio: 0.4,
    updated: 1,
    still_unknown: ['城墙南门门票', '永兴坊美食'],
    summary: '补上 1 个价格；1 个查了没查到（保持未核实）；价格覆盖：可核实 40% / 未取到 40%',
    pending_summary: '2 项需要你确认：1 个住宿节点高德不提供房价；1 个节点没有可核实报价。点右侧入口可直接去核价。',
    pending: [
      { name: '钟楼酒店', kind: 'hotel', reason: '高德不提供房价，需要你确认', url: 'https://uri.amap.com/marker?position=108.94,34.26&name=x' },
      { name: '城墙南门门票', kind: 'cultural', reason: '没有可核实报价，需要你确认', url: 'https://www.amap.com/search?query=x' },
    ],
  },
  // 跨城腿比较：票价没有可核实来源时只比时长，并明确标注未核实（core/transport_options）
  // 注意 `price: null` 是**故意**的：真实后端在票价未核实时就会下发 null，
  // 而前端曾经在流式累积阶段把 "null" 全部删掉，把整段 JSON 弄坏、核对面板整块消失。
  transport_audit: {
    legs: [
      {
        from: '西安',
        to: '成都',
        distance_km: 658.3,
        after_day: 7,
        before_day: 8,
        advice: '建议高铁：约 4 小时 10 分，二等座票价未核实',
        alternatives: ['飞机 约 1 小时 30 分（票价未核实）'],
        unverified_fares: [{ mode: '高铁', code: 'G1701', reason: '无票务来源' }],
        options: [{ mode: '高铁', duration_minutes: 250, transfers: 0, price: null, fare_verified: false }],
      },
    ],
    note: '跨城段的票价需要可核实的票务来源；缺来源时只比较时长，并明确标注未核实。',
  },
  // 长途分段摘要：哪段需要修补、哪段被重新生成、哪段没补上（core/long_trip.summarize_segments）
  long_trip: {
    segments: [
      { index: 1, start_day: 1, end_day: 7, days: 7, nodes: 21, rest_days: 2, needs_repair: false },
      { index: 2, start_day: 8, end_day: 14, days: 7, nodes: 18, rest_days: 1, needs_repair: true },
      { index: 3, start_day: 15, end_day: 16, days: 2, nodes: 5, rest_days: 0, needs_repair: false },
    ],
    ok: false,
    // 首轮：按 7 天一段逐段生成后合并（core/long_trip.build_segmented_plan → agent.py 薄接线）
    first_round: {
      chunk_days: 7,
      generated: [1, 2, 3],
      failed: [],
      skipped: [],
      truncated: false,
      out_of_range_days: [17],
    },
    repaired: [{ segment: 2, days: '8-14', added: 3, dropped_out_of_range: [17], reasons: ['long_trip_day_gap'] }],
    repair_failed_segments: [15],
  },
  // 自动复核（core/plan_review.review_plan）：确定性修掉了什么、还剩几处、缺哪些数据
  review: {
    stopped_reason: 'needs_data',
    rounds: 2,
    action_count: 4,
    actions: [
      { code: 'reschedule', day: 1, node: '陕西历史博物馆', from: '12:30', to: '09:30' },
      { code: 'reschedule', day: 1, node: '回民街小吃', from: '12:30', to: '11:00' },
      { code: 'dedupe', day: 2, dropped: ['城墙南门'], count: 1 },
      { code: 'add_meal', day: 3, node: '永兴坊小吃（候选池）', intent: 'food', source: 'amap', detail: '这一天没有餐饮节点' },
    ],
    initial_score: 68.4,
    final_score: 72.1,
    initial_hard_failures: 4,
    remaining_hard: 0,
    needs_data: ['needs_candidates'],
    used_pool: true,
  },
  // 用户诉求逐条核对（core/constraint_coverage）：稳定 id + 状态，前端逐条渲染
  constraints: {
    total: 6,
    summary: '共 6 条诉求：落实 3 · 部分落实 1 · 未核实 1 · 仅建议 0 · 没做到 1',
    counts: { applied: 3, partially_applied: 1, unverified: 1, advisory: 0, missing: 1 },
    unverified_ids: ['dietary'],
    missing_ids: ['interest:scenic'],
    items: [
      { id: 'must_have:酒店', category: 'must_have', text: '必须包含「酒店」', status: 'applied', polarity: 'require', source: 'user_explicit', detail: '' },
      { id: 'budget', category: 'budget', text: '预算 ¥1200 以内', status: 'applied', polarity: 'require', source: 'user_explicit', detail: '可核实花费 ¥1164 在预算内' },
      { id: 'interest:food', category: 'interest', text: '美食', status: 'applied', polarity: 'prefer', source: 'signal', detail: '' },
      { id: 'interest:scenic', category: 'interest', text: '自然风光', status: 'missing', polarity: 'prefer', source: 'signal', detail: '方案里没有这类内容' },
      { id: 'dietary', category: 'accessibility', text: '饮食要求：不吃辣', status: 'unverified', polarity: 'require', source: 'user_explicit', detail: '没有菜品级数据，是否满足无法核实' },
      { id: 'quota', category: 'change', text: '增加：美食 +2', status: 'partially_applied', polarity: 'require', source: 'user_explicit', detail: '要 2 个，只加上 1 个' },
    ],
  },
  // 数据降级登记（core/degradation.collect_degradations）：文案与"用户损失"都由后端注册表给出
  degradations: {
    total: 2,
    sources: ['hotel_price', 'candidate_pool'],
    counts: { missing: 1, degraded: 1 },
    summary: '2 项数据不完整或没拿到：住宿价格、候选地点池',
    items: [
      { source: 'hotel_price', label: '住宿价格', status: 'missing', reason: '2 个住宿节点都没拿到报价', impact: '住宿花费无法核实（高德对酒店普遍不返回价格），预算结论只能按缺价处理' },
      { source: 'candidate_pool', label: '候选地点池', status: 'degraded', reason: '候选池里没有更多可用的同类地点', impact: '缺玩点/缺餐/住宿夜数不够时无法自动补，只能如实告诉你' },
      { source: 'llm_plan', label: '多智能体推演', status: 'degraded', reason: '大模型不可用，本次方案改由高德候选池合成', impact: '本次方案是候选池保底合成（不是模型推演结果），可稍后重新推演一次' },
    ],
  },
  // 住宿联合评估（core/joint_optimization.lodging_joint_payload，agent.py 在路线定型后挂上）：
  // "住哪"不只看房价，还要看每天往返的通勤时间/费用/体力/碳排/预算余额，且各有取舍的选项都保留。
  lodging_joint: {
    status: 'evaluated',
    note: '成本只统计已知分项；未知分项不参与“更便宜”的比较。',
    tradeoffs: [
      '最省：钟楼青旅（已知成本 ¥461）',
      '最少通勤：钟楼酒店（42 分钟）',
      '碳排最低：钟楼酒店（3.1 kg，方法学估算）',
    ],
    dominated: {},
    candidates: [
      {
        lodging_name: '钟楼酒店',
        nights: 2,
        lodging_total: 600,
        lodging_price_unknown: false,
        commute_minutes_total: 42,
        commute_cost: 26,
        commute_time_value: 50.4,
        switch_penalty: 0,
        total_cost_known: 676.4,
        unknown_components: [],
        playable_nodes: 6,
        fatigue_index: 4.2,
        carbon_kg: 3.1,
        carbon_source: 'fallback_constant',
        budget_remaining: 523.6,
        estimated: true,
      },
      {
        lodging_name: '钟楼青旅',
        nights: 2,
        lodging_total: 300,
        lodging_price_unknown: false,
        commute_minutes_total: 96,
        commute_cost: 46,
        commute_time_value: 115.2,
        switch_penalty: 0,
        total_cost_known: 461.2,
        unknown_components: [],
        playable_nodes: 5,
        fatigue_index: 7.8,
        carbon_kg: 5.4,
        carbon_source: 'fallback_constant',
        budget_remaining: 738.8,
        estimated: true,
      },
      {
        // 房价取不到：必须显示成"未取到"，不能当成 0 元，也不能拿它去比"更便宜"
        lodging_name: '城西远郊酒店',
        nights: 2,
        lodging_total: null,
        lodging_price_unknown: true,
        commute_minutes_total: 168,
        commute_cost: 92,
        commute_time_value: 201.6,
        switch_penalty: 120,
        total_cost_known: 413.6,
        unknown_components: ['lodging_price'],
        playable_nodes: 4,
        fatigue_index: 12.6,
        carbon_kg: 9.2,
        carbon_source: 'fallback_constant',
        budget_remaining: 786.4,
        estimated: true,
      },
    ],
  },
};

async function installMocks(page: Page) {
  savedTripBodies = [];
  adoptCalls = [];
  variantSaves = [];
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
            { day: 1, location: '钟楼酒店', name: '钟楼酒店', time: '09:00', type: '住宿', cost_estimate: '¥300', tags: ['住宿'] },
            { day: 1, location: '陕西历史博物馆', name: '陕西历史博物馆', time: '10:30', type: '博物馆', cost_estimate: '¥0', tags: ['文化'] },
            { day: 1, location: '回民街小吃', name: '回民街小吃', time: '12:30', type: '餐饮', cost_estimate: '¥60', tags: ['美食'] },
            { day: 2, location: '城墙南门', name: '城墙南门', time: '09:30', type: '文化', cost_estimate: '¥54', tags: ['地标'] },
            { day: 2, location: '永兴坊美食', name: '永兴坊美食', time: '12:30', type: '餐饮', cost_estimate: '¥70', tags: ['美食'] },
          ];
          setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({ type: 'target_city', payload: { name: '西安', lnglat: [108.94, 34.26] } }),
            });
          }, 20);
          setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({
                type: 'stream_token',
                payload:
                  '[FINAL_JSON]' +
                  JSON.stringify({
                    status: 'degraded_fallback',
                    negotiation_summary: '已按预算与偏好完成协商',
                    route,
                    team_satisfaction: { 'E2E 旅行者': 62 },
                    // ai-service 在 final_route 里回填的关联键：前端必须随行程一起存下去，
                    // 否则网关侧的"采纳/到访/重规划"事件只能退回 trip_id，跨服务轨迹串不起来
                    correlation_id: 'plan-e2e-1',
                    quality: payload.governance.quality,
                    budget_report: payload.governance.budget_report,
                    fallback: payload.governance.fallback,
                    horizon: payload.governance.horizon,
                    increment: payload.governance.increment,
                    price_audit: payload.governance.price_audit,
                    transport_audit: payload.governance.transport_audit,
                    long_trip: payload.governance.long_trip,
                    review: payload.governance.review,
                    constraints: payload.governance.constraints,
                    degradations: payload.governance.degradations,
                    lodging_joint: payload.governance.lodging_joint,
                  }),
              }),
            });
          }, 80);
          // 生产里后端在保底模式下会额外下发一条 final_route（status=degraded_fallback），
          // 这里照实补上，才能验证"保底路线"标记是真的会显示出来。
          setTimeout(() => {
            this.onmessage?.({
              data: JSON.stringify({
                type: 'final_route',
                payload: { status: 'degraded_fallback', route, negotiation_summary: '已按预算与偏好完成协商' },
              }),
            });
          }, 120);
        }
        close() {
          this.readyState = 3;
        }
        addEventListener() {}
        removeEventListener() {}
      }
      (window as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
    },
    { user, governance: GOVERNANCE_PAYLOAD },
  );

  await page.route('**/api/v1/**', (route) => route.fulfill({ json: { ok: true, success: true, data: {} } }));
  await page.route('**/api/auth/login', (route) => route.fulfill({ json: { message: '登录成功', token: user.token, user } }));
  await page.route('**/api/user/**', (route) => route.fulfill({ json: { ok: true, user, trips: [] } }));
  // 具体路由要注册在通配路由**之后**（Playwright 后注册者优先），否则会被通配吃掉
  await page.route('**/api/user/trip', async (route) => {
    try {
      savedTripBodies.push(route.request().postDataJSON());
    } catch {
      savedTripBodies.push(null);
    }
    await route.fulfill({ json: { message: '行程已保存', trip_id: 'trip-e2e' } });
  });
  // 采纳链路：方案先落库拿服务端 id，再 adopt（会发出 user.accepted 事件）
  await page.route('**/api/v1/plan/variant', async (route) => {
    variantSaves.push(route.request().postDataJSON());
    await route.fulfill({ json: { message: '方案已保存', variant: { id: 'variant-e2e', name: 'AI 主方案' } } });
  });
  await page.route('**/api/v1/plan/variant/adopt', async (route) => {
    adoptCalls.push(route.request().postDataJSON());
    await route.fulfill({ json: { message: '方案已采纳' } });
  });
  await page.route('**/api/community/**', (route) => route.fulfill({ json: { ok: true, posts: [], tags: [] } }));
}

async function reachDecision(page: Page) {
  await page.getByTestId('join-room-code').fill('ROOM-E2E');
  await page.getByTestId('join-room-submit').click();
  await expect(page.getByTestId('room-code')).not.toBeEmpty();
  await page.getByTestId('begin-plan').click();
  await expect(page.getByText('进入共识沙盘')).toBeVisible({ timeout: 10_000 });
  await page.getByText('进入共识沙盘').click();
  await expect(page.getByText('你的个性化旅行诉求')).toBeVisible({ timeout: 20_000 });
  await page.locator('textarea').first().fill('带孩子去西安 3 天，预算 1200，必须住得下也要每天有正餐');
  await page.getByText('生成行程方案').click();
  await expect(page.getByText('路线共识解释')).toBeVisible({ timeout: 20_000 });
}

test.describe('行程核对面板（预算/兜底/超长行程/遗留问题）', () => {
  test('渲染四段说明并留下视觉基线截图', async ({ page }) => {
    // 这条要走完整流程 + 截图，机器负载高时 45s 会不够（实测在并发构建时超时过）
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page);

    // 方案生成后会自动保存行程：内容里必须带 correlation_id，否则跨服务轨迹串不起来
    const savedWithCorrelation = savedTripBodies.find((body) => String(body?.content || '').includes('plan-e2e-1'));
    expect(
      savedWithCorrelation,
      `保存行程时应带上 correlation_id（实际收到 ${savedTripBodies.length} 次保存：${JSON.stringify(savedTripBodies.map((b) => b?.content?.slice(0, 80)))}）`,
    ).toBeTruthy();

    // 自动保存 ≠ 采纳：它只是系统落库，不能上报 user.accepted（否则方案一生成就"被采纳"）
    expect(adoptCalls.length, `自动保存不该触发采纳上报，实际 ${adoptCalls.length} 次`).toBe(0);

    // 用户点「保存到我的行程」= 明确选定当前方案 → 必须上报一次采纳
    await page.getByTestId('save-trip').click();
    await expect.poll(() => adoptCalls.length, { timeout: 10_000 }).toBeGreaterThan(0);
    expect(String(adoptCalls[0]?.variant_id || '')).not.toBe('');
    expect(String(adoptCalls[0]?.user_id || '')).not.toBe('');

    // Governance is intentionally kept in the evidence layer so the default
    // plan view stays focused. Open that layer before asserting its details.
    await page.getByTestId('workspace-layer-evidence').click();

    const panel = page.getByLabel('行程核对说明');
    await expect(panel).toBeVisible({ timeout: 10_000 });

    // 预算核对：三级模型的措辞必须在（"已核实"而不是"AI 估算总价"）
    await expect(panel.getByText('预算核对')).toBeVisible();
    await expect(panel.getByText('已核实花费', { exact: true })).toBeVisible();
    await expect(panel.getByText('可能超支')).toBeVisible();
    await expect(panel.getByText('估算花费（未核实）', { exact: true })).toBeVisible();

    // 已作调整：平替明细 + 人话说明
    await expect(panel.getByText('已作调整')).toBeVisible();
    await expect(panel.getByText('把「钟楼酒店」换成「青旅床位」')).toBeVisible();
    await expect(panel.getByText(/已核实花费」少约/)).toBeVisible();

    // 行程规模：超长行程分段
    await expect(panel.getByText('行程规模')).toBeVisible();
    await expect(panel.getByText('16 天 · 3 段（7/7/2）')).toBeVisible();

    // 本次调整：达成几项 + 对其它安排的影响（说"响应度"，不说"AI 已优化"）
    await expect(panel.getByText('本次调整')).toBeVisible();
    await expect(panel.getByText('响应度 71%')).toBeVisible();
    await expect(panel.getByText('你要的调整', { exact: true })).toBeVisible();
    await expect(panel.getByText('实际达成', { exact: true })).toBeVisible();
    await expect(panel.getByText('对其余安排的影响', { exact: true })).toBeVisible();
    await expect(panel.getByText(/这次你要的 2 项调整都已落实/)).toBeVisible();

    // 价格核对：比例 + 还需补价的节点（未核实必须写明，不得当成 0 元）
    await expect(panel.getByText('价格核对')).toBeVisible();
    await expect(panel.getByText('已核实 2 项 · 需要你确认 2 项')).toBeVisible();
    // "需要你确认"要逐条说清原因，并给可点开的核价入口（酒店房价尤其要写明高德不提供）
    await expect(page.getByTestId('price-pending')).toBeVisible();
    await expect(panel.getByText('高德不提供房价，需要你确认')).toBeVisible();
    await expect(panel.getByText('没有可核实报价，需要你确认')).toBeVisible();
    await expect(panel.getByRole('link', { name: '去核价' }).first()).toHaveAttribute('href', /^https:\/\//);
    await expect(panel.getByText(/还需补价：城墙南门门票、永兴坊美食/)).toBeVisible();
    await expect(panel.getByText(/补上 1 个价格/)).toBeVisible();

    // 住哪更划算：房价之外的连锁影响（每天往返通勤/交通费/体力/碳排/预算余额）
    // 取不到的房价必须写成"未取到"，不能当成 0 元，也不能拿它去比"更便宜"。
    await expect(panel.getByText('住哪更划算')).toBeVisible();
    await expect(page.getByTestId('lodging-joint')).toBeVisible();
    await expect(panel.getByText('住 2 晚 ¥600').first()).toBeVisible();
    await expect(panel.getByText(/每天往返/).first()).toBeVisible();
    await expect(panel.getByText('42 分钟').first()).toBeVisible();
    await expect(panel.getByText(/交通约 ¥26/).first()).toBeVisible();
    await expect(panel.getByText(/预算余额 ¥524/).first()).toBeVisible();
    await expect(panel.getByText('房价未取到').first()).toBeVisible();
    await expect(panel.getByText(/未取到：房价（不计入总价，也不当成 0 元）/).first()).toBeVisible();
    await expect(panel.getByText(/最省：钟楼青旅/).first()).toBeVisible();
    await expect(panel.getByText(/最少通勤：钟楼酒店/).first()).toBeVisible();

    // 跨城怎么走：出行建议 + 备选 + 票价未核实
    await expect(panel.getByText('跨城怎么走')).toBeVisible();
    await expect(panel.getByText(/西安 → 成都/)).toBeVisible();
    await expect(panel.getByText(/建议高铁：约 4 小时 10 分，二等座票价未核实/)).toBeVisible();
    await expect(panel.getByText(/备选：飞机 约 1 小时 30 分（票价未核实）/)).toBeVisible();

    // 长途分段：段级节点数/休整日 + 首轮按段生成的结果 + 哪段重生成过、哪段没补上
    await expect(panel.getByText('长途分段')).toBeVisible();
    await expect(panel.getByText('有待修补')).toBeVisible();
    await expect(panel.getByText(/按 7 天一段逐段生成：3 段已排好/)).toBeVisible();
    await expect(panel.getByText(/模型多给了第 17 天，已丢弃/)).toBeVisible();
    await expect(panel.getByText(/第 2 段 · 第 8-14 天/)).toBeVisible();
    await expect(panel.getByText(/18 个节点 · 休整 1 天/)).toBeVisible();
    await expect(panel.getByText(/已重新生成 1 段（第 8-14 天）；未补上 1 段（从第 15 天起）/)).toBeVisible();

    // 诉求核对：逐条给状态；"未核实"绝不能写成"已满足"
    await expect(panel.getByText('诉求核对')).toBeVisible();
    await expect(panel.getByText('6 条', { exact: true })).toBeVisible();
    await expect(panel.getByText('已落实', { exact: true }).first()).toBeVisible();
    await expect(panel.getByText('未核实', { exact: true }).first()).toBeVisible();
    await expect(panel.getByText('没做到', { exact: true }).first()).toBeVisible();
    await expect(panel.getByText('必须包含「酒店」')).toBeVisible();
    await expect(panel.getByText(/饮食要求：不吃辣/)).toBeVisible();
    await expect(panel.getByText(/没有菜品级数据，是否满足无法核实/)).toBeVisible();
    await expect(panel.getByText('部分落实', { exact: true }).first()).toBeVisible();

    // 自动复核：改了什么（含"从候选池补点"）、还剩几处、哪些改不动
    await expect(panel.getByText('自动复核')).toBeVisible();
    await expect(panel.getByText('4 处已修好 · 没有遗留问题')).toBeVisible();
    await expect(panel.getByText(/已自动修正：重排时间 2 处 · 去掉重复 1 处 · 补餐饮 1 处/)).toBeVisible();
    await expect(panel.getByText(/第 1 天「陕西历史博物馆」/)).toBeVisible();
    await expect(panel.getByText(/这些我改不动，要靠数据或你确认：候选池补点/)).toBeVisible();

    // 遗留问题：说人话的标签而不是错误码
    await expect(panel.getByText('还需要你留意')).toBeVisible();
    await expect(panel.getByText('某一天没有游玩安排', { exact: true })).toBeVisible();
    await expect(panel.getByText('营业时间未核实', { exact: true })).toBeVisible();

    // 保底路线必须一眼看出来（不是正常推演结果），核对面板里也要有这条降级
    await expect(page.getByTestId('fallback-route-badge')).toBeVisible();
    await expect(page.getByTestId('fallback-route-badge')).toContainText('非智能体推演');
    await expect(panel.getByText('多智能体推演')).toBeVisible();
    await expect(panel.getByText(/大模型不可用，本次方案改由高德候选池合成/)).toBeVisible();

    // 数据降级：哪个数据源没拿到、用户损失什么（"没拿到"≠"已满足"）
    await expect(panel.getByText('数据降级')).toBeVisible();
    await expect(panel.getByText('住宿价格')).toBeVisible();
    await expect(panel.getByText(/2 个住宿节点都没拿到报价/)).toBeVisible();
    await expect(panel.getByText(/住宿花费无法核实/)).toBeVisible();
    await expect(panel.getByText('候选地点池')).toBeVisible();

    // 去 AI 化检查：面板内不得出现这类措辞
    const text = (await panel.innerText()).toLowerCase();
    for (const banned of ['ai ', 'ai已', '智能推荐', '一键生成', '✨', '🚀']) {
      expect(text.includes(banned), `面板不应出现「${banned}」`).toBeFalsy();
    }

    // 版面回归（血的教训）：面板曾经挂在 header 里，而 header 是 auto 高度、不可滚动，
    // 8 段内容把整列顶到 1200px+，于是①正文区被压成 0 高、行程完全看不见②面板最后几段
    // 滚不到（1440×900 实测：header 1210px、正文 clientHeight 0、文档不可滚）。
    // 现在面板在滚动区里：这里直接找它**真正所在的可滚动祖先**，把它 scrollIntoView 到
    // 视口中央，再验证「确实滚进来了」——如果容器不可滚（旧 bug），这段就会留在视口外。
    // 「行程核对面板」在证据层、「行程正文」在主方案层，两者已不再同屏，
    // 因此分层各自验证，而不是一次性在文档里同时找两个元素。
    const probeLayout = async (which: 'governance' | 'day'): Promise<any> => page.evaluate((target) => {
      const scrollerOf = (el: HTMLElement) => {
        let node: HTMLElement | null = el.parentElement;
        while (node && node !== document.body) {
          const style = getComputedStyle(node);
          if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 4) return node;
          node = node.parentElement;
        }
        return null;
      };
      const probe = (el: HTMLElement | null) => {
        if (!el) return { found: false, inView: false };
        const scroller = scrollerOf(el);
        if (!scroller) return { found: true, scroller: false, inView: false };
        el.scrollIntoView({ block: 'center' });
        const rect = el.getBoundingClientRect();
        return {
          found: true,
          scroller: true,
          inView: rect.top >= 0 && rect.bottom <= window.innerHeight,
          top: Math.round(rect.top),
          bottom: Math.round(rect.bottom),
          clientHeight: scroller.clientHeight,
          scrollHeight: scroller.scrollHeight,
        };
      };
      const all = Array.from(document.querySelectorAll<HTMLElement>('*'));
      const el = target === 'governance'
        ? (all.filter((node) => node.textContent === '还需要你留意').pop() || null)
        : (all.find((node) => /^第 \d+ 天 行程安排$/.test(node.textContent || '')) || null);
      return probe(el);
    }, which);

    // 证据层：面板最后一段必须能滚进视口，且它所在滚动区必须有实际高度
    const governanceLayout = await probeLayout('governance');
    expect(governanceLayout.inView, `面板最后一段必须能滚进视口：${JSON.stringify(governanceLayout)}`).toBe(true);
    expect(governanceLayout.clientHeight || 0, '滚动区必须有实际高度').toBeGreaterThan(200);

    // 回到面板顶部再截图：基线要稳定，不能受上一段滚动位置影响
    await panel.evaluate((el) => el.scrollIntoView({ block: 'start' }));

    // 截图时禁用动画：页面里的 framer-motion / 3D 画布会一直动，
    // 不禁用的话 Playwright 会等"字体加载 + 动画稳定"直到超时。
    await page.screenshot({ path: '../work/plan-governance.png', animations: 'disabled', fullPage: false });

    // 主方案层：行程正文（第 N 天 行程安排）必须存在并能滚进视口。
    // 面板迁到证据层后，正文只属于主方案层 —— 必须切回 plan 层再验，
    // 否则文档里根本没有这个标题（旧断言就是因此失效）。
    await page.getByTestId('workspace-layer-plan').click();
    const dayLayout = await probeLayout('day');
    expect(dayLayout.found, '行程正文（第 N 天 行程安排）必须存在').toBe(true);
    expect(dayLayout.inView, `行程正文必须能滚进视口：${JSON.stringify(dayLayout)}`).toBe(true);
  });
});
