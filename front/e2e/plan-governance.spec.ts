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
// 参考路线学习：粘贴 → 保存 → 界面要能看到"学到了什么"，方案里要能看到依据
let referenceSaves: any[] = [];
// 截图识别：上传图片 → 出文字 → 用户核对后再学习（识别本身不许写画像）
let ocrCalls: any[] = [];
let ocrMode: 'ok' | 'no_text' = 'ok';

// 一张最小合法 PNG（1×1）：E2E 会把识别接口 mock 掉，这里只验证"文件真的被读成 data URL 发出去了"
const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==',
  'base64',
);
const OCR_TEXT = 'Day1 上午 博斯腾湖 环湖骑行 门票45元\n住宿 孔雀河边民宿 一晚180元';

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
    // 联网取价的实际结果（专业诊断里要显示"查了几个、补到几条、来源通不通"）
    provider_reachable: true,
    provider_status: {},
    looked_up: 5,
    filled_from_web: 2,
    not_looked_up: 1,
    timed_out: 2,
    budget_seconds: 45,
    // "有数字但只有网摘出处"的升级台账（core/price_sources.unsourced_price_claims +
    // agent.py 取价第二轮）：点开只有网摘的价格，系统有没有试着换成官方页面
    unsourced_upgrade_attempted: 2,
    upgraded_to_verifiable: ['博斯腾湖'],
    unsourced_after: ['城墙南门门票'],
    // 酒店实时房价（供应商只读 MCP）：查了几家、匹配到几家、其中几家给了数字
    rollinggo: {
      enabled: true,
      reason: '已配置只读酒店查询',
      queried: 2,
      matched: 2,
      with_price: 1,
      prices: { '库尔勒园林宾馆': 316 },
      without_price: ['瑞德假日酒店(巴州博物馆市政府店)'],
      not_found: [],
      check_in: '（未给出发日期，供应商按明日报价）',
      nights: 1,
    },
    pending: [
      { name: '钟楼酒店', kind: 'hotel', reason: '高德不提供房价，需要你确认', url: 'https://uri.amap.com/marker?position=108.94,34.26&name=x' },
      { name: '城墙南门门票', kind: 'cultural', reason: '这是联网估算价（不是官方报价），可点开来源核对', url: 'https://www.xinjiang.gov.cn/ticket/45', estimated: true, value: 45 },
      { name: '永兴坊美食', kind: 'food', reason: '没有可核实报价，需要你确认', url: 'https://www.amap.com/search?query=x' },
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
  // 节点级负反馈的落地情况（core/feedback_signals + agent.py）：
  // 用户点过「不喜欢」的地点/写的原因，这一次到底改了没有 —— 必须渲染出来
  feedback_applied: {
    exclude_names: ['胡杨书院'],
    distance_tolerance: 'low',
    budget_tendency: null,
    notes: [
      '你上次把「胡杨书院」标为不喜欢（原因：太远了，来回太折腾），本次不再排它',
      '你在反馈里提到「太远了，来回太折腾」，本次按就近安排：当天不再安排明显超出距离预算的远点',
    ],
    counts: { negative_total: 1, excluded: 1, distance: 1, price: 0 },
  },
  // 住宿候选的供应商实价清单（core/rollinggo_mcp.search_hotels + agent.py）：
  // 同样的预算还有哪些**有实价**的可选 —— 这是"住哪"的选择权，必须渲染
  lodging_supply: {
    status: 'ok',
    count: 6,
    with_price: 6,
    added: 5,
    nightly_cap: 1080,
    min_star: 4,
    hotels: [
      { name: '库尔勒福润德·万达锦华酒店', price: 285, star: 5, address: '圣果路2号福润德大厦C座', booking_url: 'https://rollinggo.cn/pages/hotel/detail/index?id=1', nearest_play_km: 3.4, nearest_play_name: '孔雀河景观带', far_from_plays: false },
      { name: '维也纳国际酒店(库尔勒天鹅河店)', price: 694, star: 4, address: '民心路5号', booking_url: 'https://rollinggo.cn/pages/hotel/detail/index?id=2', nearest_play_km: 41.2, nearest_play_name: '博斯腾湖风景名胜区', far_from_plays: true },
    ],
  },
  // 跑得远的那天"可以换成哪个近点"（core/travel_feasibility.suggest_near_alternatives）：
  // 只提议、不改方案 —— 用户一句话就能兑现
  travel_alternatives: {
    note: '这些只是可换的近点（同类别、离当天其它点更近）；想换直接说一句就行，不说就不动。',
    days: [
      {
        day: 2,
        travel_km: 447.8,
        budget_km: 160,
        far_nodes: [{ name: '胡杨书院', distance_km: 221.4 }],
        options: [
          {
            for_node: '胡杨书院',
            for_node_km: 221.4,
            name: '喀什地区博物馆',
            category: 'cultural',
            center_km: 3.2,
            nearest_day_km: 4.6,
            price: 0,
            why: '替换「胡杨书院」（离市中心约 221km）：这个点在市中心 3km 内、离当天其它点约 5km',
          },
        ],
      },
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
  // 参考路线画像披露（后端只在"当轮诉求没说明白"时才用它，并回填依据）
  reference_profile: {
    applied: true,
    reference_count: 1,
    reasons: ['参考路线里自然 8 处 / 人文 0 处', '参考路线节奏「relaxed」→ 每天约 3 个节点'],
    disclosure: '这次没有额外指定偏好，所以按你参考过的 1 条路线来的：参考路线里自然 8 处 / 人文 0 处。想改就直接说（例如「这次多看人文」），诉求会覆盖画像。',
  },
  // 候选供给：这次方案里的地点有多少真的来自数据源（真实探针里 库尔勒=景点22/自然18/美食3）
  candidate_supply: {
    status: 'thin',
    needed_daytime: 10,
    known_daytime: 7,
    attractions: 4,
    foods: 3,
    scenic: 1,
    widened: ['自然景观'],
    notes: ['真实白天候选 7 个，少于排满 10 个白天节点所需的量；部分地点靠模型知识补，请核对'],
    out_of_town_scenic: 2,
    farthest_scenic_km: 76,
  },
  // 行程配比（用户口径）：默认自然景区与人文各半、穿插美食住宿；自然只算景区
  composition: {
    scenic: 4,
    cultural: 4,
    food: 2,
    hotel: 1,
    daytime: 10,
    source: 'default_balanced',
    actual: { scenic: 3, landmark: 3, city_park: 2, cultural: 2, food: 2, hotel: 1, shopping: 0, other: 0 },
    audit: {
      ok: false,
      notes: ['人文 2 个，目标 4 个（偏少）'],
      deviations: [{ intent: 'cultural', target: 4, actual: 2, severity: 'under' }],
    },
  },
  // 协商纪要：谁参与、站在什么立场、争了什么、裁决凭什么、谁没参与
  negotiation: {
    facts_version: 'facts-v1-e2etest1',    agents: [
      { agent_id: 'route-planner', domain: 'itinerary', prefers: '远景区', candidates: 12, status: 'ok' },
      { agent_id: 'health-agent', domain: 'health', prefers: '保守强度', candidates: 1, status: 'ok' },
      { agent_id: 'dining-agent', domain: 'dining', prefers: '本地餐饮', candidates: 9, status: 'ok' },
    ],
    inactive: [{ agent_id: 'budget-agent', reason: 'candidate_pool_empty' }],
    conflicts: [
      {
        code: 'far_scenic_vs_daily_intensity',
        between: ['route-planner', 'health-agent'],
        detail: '路线侧有 2 个远景区（博斯腾湖、罗布人村寨），体力侧建议每天最多 4 个节点',
        severity: 'negotiable',
      },
    ],
    arbitration: [
      {
        code: 'far_scenic_vs_daily_intensity',
        decision: '保留远景区，但**一天最多一个**，其余移出当天',
        mechanism: 'core/travel_feasibility.repair_day_overload（硬约束过滤阶段执行）',
        why: '你明确表示不介意跑远',
      },
    ],
    notes: ['没有参与博弈的智能体：budget-agent'],
    revision: {
      round: 1,
      revised_agents: ['route-planner'],
      conflicts_before: ['far_scenic_vs_daily_intensity'],
      conflicts_after: [],
      resolved: ['far_scenic_vs_daily_intensity'],
      changed: [
        {
          agent_id: 'route-planner',
          applied: { max_far_scenic: { limit: 3, dropped_count: 10, dropped: ['胡杨林', '自然草原'] } },
          candidates_after: 3,
        },
      ],
      notes: ['协商一轮后分歧全部解决'],
      convergence: { code: 'converged', reason: '分歧已全部解决，协商结束' },
    },
  },
  // 统一事实记录（闭环第①段）：每条事实都带 source（谁说的）与 verified（能不能当真）。
  // 这里**故意**混进一条 source=unknown 的（预算）：界面上必须显示"未取到"，
  // 绝不能显示成 0 —— "不知道"和"是 0"是两件事。
  facts: {
    schema_version: 1,
    facts_version: 'facts-v1-e2etest1',
    request: {
      city: { value: '库尔勒', source: 'user_intent', verified: true },
      trip_days: { value: 2, source: 'user_intent', verified: true },
      budget_total: { value: null, source: 'unknown', verified: false, note: '用户没给预算' },
      requirements: { value: ['自然风景', '购物商圈'], source: 'user_intent', verified: true },
    },
    constraints: {
      exclude_cultural: { value: false, source: 'user_intent', verified: true },
      park_cap: { value: 2, source: 'policy', verified: true, note: '城市公园每天上限' },
    },
    suppliers: {
      amap_attractions: { value: 16, source: 'amap', verified: true },
      supply_status: { value: 'ok', source: 'derived', verified: true },
      widened: { value: ['美食'], source: 'policy', verified: true },
    },
    decisions: {
      composition_targets: {
        value: { scenic: 4, cultural: 4, food: 2, hotel: 1, source: 'default_balanced' },
        source: 'policy',
        verified: true,
      },
      profile_applied: { value: null, source: 'unknown', verified: false },
    },
    outcome: {
      nodes: { value: 12, source: 'derived', verified: true },
      by_category: { value: { scenic_landmark: 4, city_park: 2, cultural: 1, food: 3, hotel: 1 }, source: 'derived', verified: true },
    },
    gaps: [
      { area: 'candidate_pool', status: 'degraded', reason: '人文候选只有 3 个（按你的偏好目标 4 个），这是候选供给的限制' },
      { area: 'price_web', status: 'degraded', reason: '未取到 40% 的价格' },
    ],
  },
};

async function installMocks(page: Page) {
  savedTripBodies = [];
  adoptCalls = [];
  variantSaves = [];
  referenceSaves = [];
  ocrCalls = [];
  ocrMode = 'ok';
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
          // 记下前端发出的 WS 请求：一键换近点这类交互必须能验证"到底发了什么"，
          // 否则只能断言"点了按钮"，无法证明意图真的带上了"不想跑那么远"。
          const w = window as unknown as { __wsSends?: unknown[] };
          w.__wsSends = [...(w.__wsSends || []), request];
          if (request.type !== 'agent_negotiate') return;
          const route = [
            { day: 1, location: '钟楼酒店', name: '钟楼酒店', time: '09:00', type: '住宿', cost_estimate: '¥300', tags: ['住宿'], data_sources: { cost_estimate: 'rollinggo' }, price_excerpt: '查价成功。1晚总价：300CNY' },
            {
              day: 1,
              location: '陕西历史博物馆',
              name: '陕西历史博物馆',
              time: '10:30',
              type: '博物馆',
              cost_estimate: '¥0',
              tags: ['文化'],
              // 官方价：来自白名单里的官方页 → 前端必须标成"可核实"并给出处链接
              data_sources: { cost_estimate: 'web_page' },
              price_source_url: 'https://www.sxhm.com/ticket',
              price_excerpt: '门票价格 门票0元/人（免费预约）',
            },
            {
              day: 1,
              location: '回民街小吃',
              name: '回民街小吃',
              time: '12:30',
              type: '餐饮',
              cost_estimate: '¥60（票务平台价，非官方）',
              tags: ['美食'],
              // 票务平台价：必须标"非官方"并把票种名照原样带出来
              data_sources: { cost_estimate: 'platform' },
              price_source_url: 'https://piao.qunar.com/ticket/detail_1.html',
              price_excerpt: '回民街美食通兑券成人票',
            },
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
                    // 方案台账（后端为"吃得着、住得近"做的结构性改动）：正文上必须看得见
                    meal_topup: {
                      actions: [
                        { code: 'move_meal', day: 2, node: '永兴坊美食', from_day: 1, distance_km: 27.7, detail: '' },
                      ],
                      skipped: [
                        { day: 1, nearest_km: 82.6, reason: '最近的饭点在 82.6km 外（超过 30km 上限），宁可不塞' },
                      ],
                    },
                    lodging_geo_adjust: {
                      actions: [{ day: 1, from: '旧钟楼旅社', to: '钟楼酒店', from_km: 46.9, to_km: 2.1 }],
                      skipped: [],
                    },
                    meal_geo_adjust: {
                      actions: [{ day: 1, from: '远在县城的馆子', to: '回民街小吃', from_km: 31.5, to_km: 1.9 }],
                      skipped: [],
                    },
                    day_rebalance: { applied: true, before_max_km: 444.5, after_max_km: 231.4, moved: ['永兴坊美食'] },
                    // 每天跑多少公里（后端**按最终路线重算**过）：超预算的那天要在正文里标明
                    travel_feasibility: {
                      budget_km: 160,
                      effective_budget_km: 176,
                      recomputed_after_closeout: true,
                      stale_before_recompute: { worst_day_km: 445.2, days: [{ day: 2, travel_km: 445.2 }] },
                      days: [
                        { day: 1, travel_km: 161.8, budget_km: 160, violations: [], far_nodes: [{ name: '陕西历史博物馆', distance_km: 66.8 }] },
                        { day: 2, travel_km: 232.7, budget_km: 160, effective_budget_km: 176, violations: ['daily_travel_budget_exceeded'], far_nodes: [] },
                      ],
                    },
                    // 文旅部名录登记的官方预约入口（按节点名索引）→ 前端要把它渲染成可点链接
                    official_profiles: {
                      陕西历史博物馆: {
                        official_name: '陕西历史博物馆',
                        official_site: 'https://www.sxhm.com/',
                        official_booking: 'https://www.sxhm.com/visit',
                        official_address: '陕西省西安市雁塔区小寨东路91号',
                        official_intro: '中国第一座大型现代化国家级博物馆，馆藏文物 170 余万件。',
                        official_miniprogram: '巴音布鲁克旅游',
                        official_channel_note: '官方声明：景区未授权任何第三方代理门票业务，请通过官方渠道购票',
                      },
                    },
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
                    reference_profile: payload.governance.reference_profile,
                    candidate_supply: payload.governance.candidate_supply,
                    composition: payload.governance.composition,
                    negotiation: payload.governance.negotiation,
                    facts: payload.governance.facts,
                    feedback_applied: payload.governance.feedback_applied,
                    lodging_supply: payload.governance.lodging_supply,
                    travel_alternatives: payload.governance.travel_alternatives,
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
                payload: {
                  status: 'degraded_fallback',
                  route,
                  negotiation_summary: '已按预算与偏好完成协商',
                  correlation_id: 'plan-e2e-1',
                  // 官方预约入口在 final_route 这条路上也要带：stream 与 final_route 两条都能到，
                  // 只补一条的话"渲染出来没有"会随到达顺序变化（实测就是这么红的）。
                  meal_topup: {
                    actions: [{ code: 'move_meal', day: 2, node: '永兴坊美食', from_day: 1, distance_km: 27.7, detail: '' }],
                    skipped: [{ day: 1, nearest_km: 82.6, reason: '最近的饭点在 82.6km 外（超过 30km 上限），宁可不塞' }],
                  },
                  lodging_geo_adjust: {
                    actions: [{ day: 1, from: '旧钟楼旅社', to: '钟楼酒店', from_km: 46.9, to_km: 2.1 }],
                    skipped: [],
                  },
                  meal_geo_adjust: {
                    actions: [{ day: 1, from: '远在县城的馆子', to: '回民街小吃', from_km: 31.5, to_km: 1.9 }],
                    skipped: [],
                  },
                  day_rebalance: { applied: true, before_max_km: 444.5, after_max_km: 231.4, moved: ['永兴坊美食'] },
                  // 每天跑多少公里（后端**按最终路线重算**过）：超预算的那天要在正文里标明
                  travel_feasibility: {
                    budget_km: 160,
                    effective_budget_km: 176,
                    recomputed_after_closeout: true,
                    stale_before_recompute: { worst_day_km: 445.2, days: [{ day: 2, travel_km: 445.2 }] },
                    days: [
                      { day: 1, travel_km: 161.8, budget_km: 160, violations: [], far_nodes: [{ name: '陕西历史博物馆', distance_km: 66.8 }] },
                      { day: 2, travel_km: 232.7, budget_km: 160, effective_budget_km: 176, violations: ['daily_travel_budget_exceeded'], far_nodes: [] },
                    ],
                  },
                  official_profiles: {
                    陕西历史博物馆: {
                      official_name: '陕西历史博物馆',
                      official_site: 'https://www.sxhm.com/',
                      official_booking: 'https://www.sxhm.com/visit',
                      official_address: '陕西省西安市雁塔区小寨东路91号',
                      official_intro: '中国第一座大型现代化国家级博物馆，馆藏文物 170 余万件。',
                      official_miniprogram: '巴音布鲁克旅游',
                      official_channel_note: '官方声明：景区未授权任何第三方代理门票业务，请通过官方渠道购票',
                    },
                  },
                },
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
  // 参考路线：GET 先返回"还没学过"，POST 返回抽取出的画像
  await page.route('**/api/v1/user/preference/reference-route', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: { data: { profile: null, reference_count: 0 } } });
      return;
    }
    if (route.request().method() === 'DELETE') {
      await route.fulfill({ json: { data: { cleared: true } } });
      return;
    }
    referenceSaves.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        data: {
          profile: {
            features: {
              category_mix: { scenic: 8, food: 5, shopping: 2 },
              pace: 'relaxed',
              time_style: 'night_owl',
              budget_tier: 'mid',
              budget_amount: 300,
              transport: ['walk', 'metro'],
              lodging_style: 'homestay',
              walking_intensity: 'high',
            },
            unknown: ['nodes_per_day'],
            confidence: 0.86,
            reference_count: 1,
            source_label: '小红书-乌鲁木齐4天',
          },
          reference_count: 1,
          evidence_kept: true,
          note: '已保存抽取出的偏好特征，以及每个特征的出处片段（用于在界面上解释依据）；整篇原文不会被保存。',
        },
      },
    });
  });
  // 截图识别：真实后端会走视觉模型；这里按契约返回识别文字或如实的失败原因
  await page.route('**/api/v1/user/preference/reference-route/ocr', async (route) => {
    ocrCalls.push(route.request().postDataJSON());
    if (ocrMode === 'no_text') {
      await route.fulfill({
        status: 422,
        json: { error: { code: 'REFERENCE_OCR_NO_TEXT_IN_IMAGE', message: '这张图里没有识别到行程文字' } },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: {
          text: OCR_TEXT,
          model: 'qwen-vl-max',
          chars: OCR_TEXT.length,
          warnings: [],
          note: '识别结果请先核对（可能有错字/漏字），确认后再点学习；服务端不保存图片。',
          privacy_note: '图片只用于本次文字识别，不会保存到服务端。',
          image_stored: false,
        },
      },
    });
  });
  await page.route('**/api/community/**', (route) => route.fulfill({ json: { ok: true, posts: [], tags: [] } }));
}

/**
 * 走到"决策阶段"。
 *
 * `referenceText` 传入时，会在**规划之前**（诉求页）走一遍参考路线学习：
 * 这是我们刻意摆放的位置 —— 画像的用途是"用户没提要求时按它排"，
 * 放到规划页就变成"先排完再用"，顺序反了。
 */
async function reachDecision(page: Page, options: { referenceText?: string } = {}) {
  await page.getByTestId('join-room-code').fill('ROOM-E2E');
  await page.getByTestId('join-room-submit').click();
  await expect(page.getByTestId('room-code')).not.toBeEmpty();
  await page.getByTestId('begin-plan').click();
  await expect(page.getByText('进入共识沙盘')).toBeVisible({ timeout: 10_000 });
  await page.getByText('进入共识沙盘').click();
  await expect(page.getByText('你的个性化旅行诉求')).toBeVisible({ timeout: 20_000 });

  if (options.referenceText) {
    // 面板默认折叠：先展开（顺序也在此验证 —— 它出现在"生成行程方案"之前）
    const referencePanel = page.getByTestId('reference-route-panel');
    await expect(referencePanel).toBeVisible();
    await expect(page.getByText('生成行程方案')).toBeVisible();
    await referencePanel.getByTestId('reference-route-toggle').click();
    await referencePanel.getByTestId('reference-route-text').fill(options.referenceText);
    await referencePanel.getByTestId('reference-route-save').click();
    const summary = page.getByTestId('reference-route-summary');
    await expect(summary).toBeVisible({ timeout: 10_000 });
    // 断言限定在摘要块里：折叠表头也会显示一条摘要（"已学 1 条：内容偏好：自然 8…"），
    // 不限定就会撞成 strict mode 冲突。
    await expect(summary.getByText(/自然 8/)).toBeVisible();
    await expect(summary.getByText(/节奏：轻松/)).toBeVisible();
    await expect(summary.getByText(/住宿：民宿/)).toBeVisible();
    await expect(referencePanel.getByText(/未识别（不会拿默认值冒充）：节奏与节点数/)).toBeVisible();
    await expect(page.getByTestId('reference-route-message')).toContainText('出处片段');
    expect(referenceSaves.length, '保存参考路线应只发一次请求').toBe(1);
    expect(referenceSaves[0]?.keep_evidence, '默认要保留出处片段（界面能解释依据）').toBe(true);

    // 勾选"只保存特征"后，请求体必须带 keep_evidence=false（隐私口径可验证）
    await referencePanel.getByTestId('reference-route-keep-evidence').check();
    await referencePanel.getByTestId('reference-route-text').fill('第二条参考：想多看人文');
    await referencePanel.getByTestId('reference-route-save').click();
    await expect.poll(() => referenceSaves.length, { timeout: 10_000 }).toBe(2);
    expect(referenceSaves[1]?.keep_evidence, '勾选后必须只存特征').toBe(false);
  }

  // 用 testid 精确定位诉求输入框：展开参考路线面板后，页面上会有两个 textarea，
  // 用 `textarea.first()` 会填错框（这个坑刚踩过）。
  await page.getByTestId('intent-input').fill('带孩子去西安 3 天，预算 1200，必须住得下也要每天有正餐');
  await page.getByText('生成行程方案').click();
  await expect(page.getByText('路线共识解释')).toBeVisible({ timeout: 20_000 });
}

test.describe('行程核对面板（预算/兜底/超长行程/遗留问题）', () => {
  test('参考路线截图识别：识别只填文字不学习，失败与不支持格式都如实说', async ({ page }) => {
    test.setTimeout(90_000);
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });

    // 走到"诉求页"（参考路线面板在规划之前就出现）
    await page.getByTestId('join-room-code').fill('ROOM-E2E');
    await page.getByTestId('join-room-submit').click();
    await expect(page.getByTestId('room-code')).not.toBeEmpty();
    await page.getByTestId('begin-plan').click();
    await expect(page.getByText('进入共识沙盘')).toBeVisible({ timeout: 10_000 });
    await page.getByText('进入共识沙盘').click();
    await expect(page.getByText('你的个性化旅行诉求')).toBeVisible({ timeout: 20_000 });

    const panel = page.getByTestId('reference-route-panel');
    await panel.getByTestId('reference-route-toggle').click();
    // 隐私口径必须在动手之前就说清楚：图片不保存、识别结果要核对
    await expect(panel.getByTestId('reference-route-ocr-privacy')).toContainText('不会保存到服务端');
    await expect(panel.getByTestId('reference-route-ocr-privacy')).toContainText('核对');

    // 1) 上传截图 → 文字填进输入框，但**不自动学习**
    await panel
      .getByTestId('reference-route-image-input')
      .setInputFiles({ name: 'xiaohongshu.png', mimeType: 'image/png', buffer: TINY_PNG });
    await expect(panel.getByTestId('reference-route-text')).toHaveValue(OCR_TEXT);
    await expect(panel.getByTestId('reference-route-ocr-note')).toContainText('核对');
    await expect(panel.getByTestId('reference-route-image-name')).toContainText('xiaohongshu.png');
    expect(ocrCalls.length, '应只发一次识别请求').toBe(1);
    expect(String(ocrCalls[0]?.image_base64 || '').startsWith('data:image/png;base64,'), '图片应以 data URL 发出').toBe(true);
    expect(referenceSaves.length, '识别不等于学习：不许自动写画像').toBe(0);

    // 2) 用户核对后手动点"学习这条路线"，这时才落库
    await panel.getByTestId('reference-route-save').click();
    await expect.poll(() => referenceSaves.length, { timeout: 10_000 }).toBe(1);
    expect(String(referenceSaves[0]?.text || '')).toContain('博斯腾湖');

    // 3) 本地就能拦掉不支持的格式：不许白发一次请求，且要说清怎么办
    await panel
      .getByTestId('reference-route-image-input')
      .setInputFiles({ name: 'x.gif', mimeType: 'image/gif', buffer: TINY_PNG });
    await expect(panel.getByTestId('reference-route-ocr-error')).toContainText('PNG');
    expect(ocrCalls.length, '格式不支持时不该发识别请求').toBe(1);

    // 4) 后端说"图里没有文字"时，如实转达原因，且不往输入框塞任何东西
    ocrMode = 'no_text';
    await panel
      .getByTestId('reference-route-image-input')
      .setInputFiles({ name: 'scenery.png', mimeType: 'image/png', buffer: TINY_PNG });
    await expect(panel.getByTestId('reference-route-ocr-error')).toContainText('没有识别到行程文字');
    await expect(panel.getByTestId('reference-route-text')).toHaveValue('');
  });

  test('渲染四段说明并留下视觉基线截图', async ({ page }) => {
    // 这条要走完整流程 + 截图，机器负载高时 45s 会不够（实测在并发构建时超时过）
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page, {
      referenceText: '乌鲁木齐4天，人均300，住民宿，南山牧场看雪山，晚上夜市吃烧烤大盘鸡，节奏轻松',
    });

    // 方案生成后会自动保存行程：内容里必须带 correlation_id，否则跨服务轨迹串不起来
    // 方案生成后会自动保存行程（由 effect 触发，可能比断言晚几百毫秒）——
    // 用轮询等待，避免"跑得快就绿、跑得慢就红"的竞态。
    await expect.poll(() => savedTripBodies.length, { timeout: 10_000 }).toBeGreaterThan(0);
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

    // 候选供给：用户必须能看到"地点有多少是查到的、多少是模型知识补的"
    const supplyBlock = panel.getByTestId('candidate-supply-block');
    await expect(supplyBlock).toBeVisible();
    await expect(panel.getByTestId('candidate-supply-counts')).toContainText('真实候选 7');
    await expect(panel.getByTestId('candidate-supply-counts')).toContainText('需要 10');
    await expect(supplyBlock).toContainText('景点 4 个（其中自然景观 1 个），餐饮 3 个');
    await expect(supplyBlock).toContainText('已额外补检索：自然景观');
    await expect(supplyBlock).toContainText('部分地点靠模型知识补，请核对');
    // 周边县市景区要说清距离（76km 的景区被排进 2 天行程，用户得知道要预留来回时间）
    await expect(panel.getByTestId('candidate-supply-outoftown')).toContainText('周边县市景区 2 个');
    await expect(panel.getByTestId('candidate-supply-outoftown')).toContainText('最远约 76 公里');

    // 配比：默认各半 + 自然只算景区（城市公园单列）+ 对不上时的偏差点
    const compositionBlock = panel.getByTestId('composition-block');
    await expect(compositionBlock).toBeVisible();
    await expect(panel.getByTestId('composition-source')).toContainText('默认：自然与人文各半');
    await expect(panel.getByTestId('composition-compare')).toContainText('目标：自然景区 4 · 人文 4 · 特色美食 2 · 住宿 1');
    await expect(panel.getByTestId('composition-compare')).toContainText('实际：自然景区 3 · 人文 2 · 特色美食 2 · 住宿 1');
    await expect(compositionBlock).toContainText('城市公园另算 2 个，不顶自然的名额');
    await expect(panel.getByTestId('composition-notes')).toContainText('人文 2 个，目标 4 个（偏少）');

    // 专业诊断（按需展开）：协商过程 + 取价出处 —— "系统怎么想的"要能查
    await page.getByTestId('workspace-layer-more').click();
    await page.getByTestId('workspace-layer-professional').click();
    const diagnostics = page.getByTestId('professional-diagnostics');
    await expect(diagnostics).toBeVisible({ timeout: 10_000 });
    const negotiationDiag = page.getByTestId('negotiation-diagnostics');
    await expect(negotiationDiag).toBeVisible();
    await expect(page.getByTestId('diagnostics-facts-version')).toContainText('facts-v1-e2etest1');
    await expect(page.getByTestId('diagnostics-stances')).toContainText('route-planner：远景区');
    await expect(page.getByTestId('diagnostics-conflicts')).toContainText('路线侧有 2 个远景区');
    await expect(page.getByTestId('diagnostics-arbitration')).toContainText('repair_day_overload');
    await expect(page.getByTestId('diagnostics-revision')).toContainText('分歧已全部解决，协商结束');
    await expect(page.getByTestId('diagnostics-inactive')).toContainText('budget-agent');
    // 价格出处：查了几个、补到几条、以及**可点开的来源链接**
    const provenance = page.getByTestId('diagnostics-price-provenance');
    await expect(provenance).toContainText('联网查了 5 个缺价节点，补到 2 条');
    await expect(provenance).toContainText('还有 1 个没查');
    // "预算用完没轮到" ≠ "网上搜不到"：这两件事后续动作不一样，必须分开说
    await expect(provenance).toContainText('2 个是取价预算用完没轮到（不是搜不到）');
    await expect(provenance).toContainText('预算 45s');
    // "有数字没出处"的台账：这些价格点开只有网摘，系统有没有试着换成官方页面要能看见
    await expect(provenance).toContainText('另有 2 个价格原先只有网摘出处，已再查一次官方页面');
    await expect(provenance).toContainText('升级为可核实 1 个（博斯腾湖）');
    // 酒店实时房价：查到没查到、报价是哪个日期的，都要能看到（"暂无供应商数据"不等于没查）
    const hotelPrice = page.getByTestId('diagnostics-hotel-price');
    await expect(hotelPrice).toContainText('查了 2 家，供应商匹配到 2 家');
    await expect(hotelPrice).toContainText('给出实价 库尔勒园林宾馆 ¥316');
    await expect(hotelPrice).toContainText('报价口径：（未给出发日期，供应商按明日报价）');
    await expect(page.getByTestId('diagnostics-pending-links')).toContainText('这是联网估算价（不是官方报价），可点开来源核对');
    // 出处链接必须指向**那条价格的真实来源**（按 href 精确定位；列表第一条是酒店的另一个链接）
    await expect(
      page.getByTestId('diagnostics-pending-links').locator('a[href="https://www.xinjiang.gov.cn/ticket/45"]'),
    ).toHaveCount(1);

    // 统一事实记录（闭环第①段）：请求/约束/供应商/决策/结果/缺口，每条带来源与核实状态
    const factsPanel = page.getByTestId('plan-facts-panel');
    await expect(factsPanel).toBeVisible();
    await expect(page.getByTestId('facts-panel-version')).toContainText('facts-v1-e2etest1');
    await expect(page.getByTestId('facts-request')).toContainText('库尔勒');
    await expect(page.getByTestId('facts-request')).toContainText('[你说的]');
    // 「不知道」不能显示成 0：没解析出预算就写"未取到"
    await expect(page.getByTestId('facts-row-budget_total')).toContainText('未取到');
    await expect(page.getByTestId('facts-constraints')).toContainText('城市公园上限：2');
    await expect(page.getByTestId('facts-constraints')).toContainText('[系统策略]');
    await expect(page.getByTestId('facts-suppliers')).toContainText('真实景点候选：16');
    await expect(page.getByTestId('facts-suppliers')).toContainText('[高德实时数据]');
    await expect(page.getByTestId('facts-decisions')).toContainText('学习画像是否生效：未取到');
    await expect(page.getByTestId('facts-outcome')).toContainText('节点数：12');
    await expect(page.getByTestId('facts-gaps')).toContainText('人文候选只有 3 个');
    await expect(page.getByTestId('facts-gaps')).toContainText('缺口清单（2 条）');

    // 回到证据层，后面的断言仍按原路径走
    await page.getByTestId('workspace-layer-evidence').click();

    // 协商纪要：谁参与 + 立场、分歧、裁决（含执行层）、谁没参与 —— 多智能体协同要看得见
    const negotiationBlock = panel.getByTestId('negotiation-block');
    await expect(negotiationBlock).toBeVisible();
    await expect(panel.getByTestId('negotiation-version')).toContainText('facts-v1-e2etest1');
    await expect(panel.getByTestId('negotiation-stances')).toContainText('route-planner：远景区（12 个候选）');
    await expect(panel.getByTestId('negotiation-stances')).toContainText('health-agent：保守强度');
    await expect(panel.getByTestId('negotiation-conflicts')).toContainText('分歧（可协商）');
    await expect(panel.getByTestId('negotiation-conflicts')).toContainText('路线侧有 2 个远景区');
    await expect(panel.getByTestId('negotiation-arbitration')).toContainText('一天最多一个');
    await expect(panel.getByTestId('negotiation-arbitration')).toContainText('repair_day_overload');
    await expect(panel.getByTestId('negotiation-inactive')).toContainText('budget-agent（candidate_pool_empty）');
    // 协商回环：分歧真的被"重提"解决了（不是只裁断一句）
    await expect(panel.getByTestId('negotiation-revision')).toContainText('协商第 1 轮');
    await expect(panel.getByTestId('negotiation-revision')).toContainText('route-planner 按裁决重提');
    await expect(panel.getByTestId('negotiation-revision')).toContainText('分歧 1 条 → 0 条');
    await expect(panel.getByTestId('negotiation-revision')).toContainText('max_far_scenic 上限 3，去掉 10 条');
    // 为什么停在这里（收敛判断）：不许默默停
    await expect(panel.getByTestId('negotiation-revision')).toContainText('分歧已全部解决，协商结束');

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

    // 按你的反馈调整：用户点过「不喜欢」之后，系统到底改了什么必须看得见
    // （后端算出来了、界面一个字没有 = 用户以为点了没用）
    const feedbackBlock = panel.getByTestId('feedback-applied-block');
    await expect(feedbackBlock).toBeVisible();
    await expect(feedbackBlock).toContainText('按你的反馈调整');
    await expect(feedbackBlock).toContainText('移出 1 个');
    await expect(feedbackBlock).toContainText('你上次把「胡杨书院」标为不喜欢（原因：太远了，来回太折腾）');
    await expect(feedbackBlock).toContainText('这次不再安排：胡杨书院');
    await expect(feedbackBlock).toContainText('按"就近"收缩当天的距离半径');

    // 远点的可换选项：跑超预算的那天，系统把"换哪个"先算出来（说一句才换）
    const alternatives = panel.getByTestId('travel-alternatives-block');
    await expect(alternatives).toBeVisible();
    await expect(alternatives).toContainText('这天想少跑点，可以换成');
    await expect(alternatives).toContainText('第 2 天（约 447.8km，当天预算 160km）');
    await expect(panel.getByTestId('travel-alternatives-list')).toContainText('胡杨书院 → 喀什地区博物馆');
    await expect(panel.getByTestId('travel-alternatives-list')).toContainText('离当天其它点约 4.6km');
    await expect(alternatives).toContainText('想换直接说一句就行，不说就不动');

    // 一键换近点：后端已经把"换成哪个近点"算好了，界面要能让用户**点一下**就换 ——
    // 只写一句"你说一声就行"等于把闭环又交回给用户。
    await page.getByTestId('swap-near-2-0').click();
    await expect(page.getByTestId('swap-near-pending')).toContainText('已按你的要求换第 2 天的远点，正在重排');
    await expect
      .poll(async () => page.evaluate(() => ((window as unknown as { __wsSends?: any[] }).__wsSends || []).length))
      .toBeGreaterThan(1);
    const swapSend = await page.evaluate(() => {
      const sends = (window as unknown as { __wsSends?: any[] }).__wsSends || [];
      return sends[sends.length - 1];
    });
    const swapIntent = String(swapSend?.payload?.user_preferences?.intent || '');
    expect(swapIntent, `换点请求应带上"不想跑那么远"这句意图，实际：${swapIntent}`).toContain('不想跑那么远');
    expect(swapIntent).toContain('第 2 天');
    // 既有路线要一起带回去（否则后端不知道在改哪一版方案）
    expect(Array.isArray(swapSend?.payload?.user_preferences?.current_existing_route)).toBe(true);

    // 住宿实价候选：同样的预算还有哪些**有实价**的可选（"住哪"的选择权）
    const lodgingSupply = panel.getByTestId('lodging-supply-block');
    await expect(lodgingSupply).toBeVisible();
    await expect(lodgingSupply).toContainText('有实价的住宿可选');
    await expect(lodgingSupply).toContainText('6 家有报价');
    await expect(panel.getByTestId('lodging-supply-list')).toContainText('库尔勒福润德·万达锦华酒店：¥285/晚（5 星）');
    await expect(panel.getByTestId('lodging-supply-list')).toContainText('维也纳国际酒店(库尔勒天鹅河店)：¥694/晚（4 星）');
    // 价格可比、距离也要可比：每家标出"离最近的玩点多远"，偏远的要显式提示通勤久
    await expect(panel.getByTestId('lodging-supply-list')).toContainText('离最近的玩点约 3.4km（孔雀河景观带）');
    await expect(panel.getByTestId('lodging-supply-list')).toContainText('离最近的玩点约 41.2km（博斯腾湖风景名胜区），通勤偏久');
    await expect(lodgingSupply).toContainText('供应商返回的可核实报价（不是估算）');
    await expect(lodgingSupply).toContainText('每晚上限 ¥1080');
    await expect(
      panel.getByTestId('lodging-supply-list').locator('a[href="https://rollinggo.cn/pages/hotel/detail/index?id=1"]'),
    ).toHaveCount(1);

    // 换住这家：清单是**只读**的话等于给了选择权却不兑现 —— 点一下要能真的换
    await page.getByTestId('swap-hotel-0').click();
    await expect(page.getByTestId('swap-near-pending')).toContainText('已按你的要求换住宿');
    const hotelSwapSend = await page.evaluate(() => {
      const sends = (window as unknown as { __wsSends?: any[] }).__wsSends || [];
      return sends[sends.length - 1];
    });
    const hotelSwapIntent = String(hotelSwapSend?.payload?.user_preferences?.intent || '');
    expect(hotelSwapIntent, `换住宿请求应点名要换的那一家，实际：${hotelSwapIntent}`).toContain(
      '住宿换成 库尔勒福润德·万达锦华酒店',
    );
    // "需要你确认"要逐条说清原因，并给可点开的核价入口（酒店房价尤其要写明高德不提供）
    await expect(page.getByTestId('price-pending')).toBeVisible();
    await expect(panel.getByText('高德不提供房价，需要你确认')).toBeVisible();
    await expect(panel.getByText('没有可核实报价，需要你确认')).toBeVisible();
    await expect(panel.getByRole('link', { name: '去核价' }).first()).toHaveAttribute('href', /^https:\/\//);
    await expect(panel.getByText(/还需补价：城墙南门门票、永兴坊美食/)).toBeVisible();
    await expect(panel.getByText(/补上 1 个价格/)).toBeVisible();

    // 住哪更划算：房价之外的连锁影响（每天往返通勤/交通费/体力/碳排/预算余额）
    // 取不到的房价必须写成"未取到"，不能当成 0 元，也不能拿它去比"更便宜"。
    // 参考画像披露：这次是按"你参考过的路线"排的，并且给出覆盖入口
    // （"按你参考过的 N 条路线"同时出现在标题与披露正文里 → 用 first() 避免 strict 冲突）
    await expect(panel.getByText('这次怎么定的口味')).toBeVisible();
    await expect(panel.getByText('按你参考过的 1 条路线').first()).toBeVisible();
    await expect(panel.getByText(/参考路线里自然 8 处/).first()).toBeVisible();
    await expect(panel.getByText(/想改就直接说/).first()).toBeVisible();

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

  // 👑 价格**出处**必须渲染出来（2026-10-07）
  //    后端这轮把门票接成了三条定向通道（官方文旅名录 / 文旅部 5A 名录 / 票务平台接口），
  //    方案里带回了票种名、来源 URL、官方预约入口 —— 但如果前端只显示"预估花销：¥60"，
  //    用户根本分不清这是官方门票、平台的哪种票、还是网摘估算（"没渲染出来的'已做'等于没做"）。
  test('价格出处：官方价 / 票务平台价 / 来源链接 / 官方预约都要看得见', async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page, {
      referenceText: '乌鲁木齐4天，人均300，住民宿，南山牧场看雪山，晚上夜市吃烧烤大盘鸡，节奏轻松',
    });
    await page.getByTestId('workspace-layer-plan').click();

    // 官方页来的价 → 标"官方价（可核实）"，并给出处链接（href 必须真指向官方页）
    const official = page.locator('[data-testid="price-provenance"]', { hasText: '官方价（可核实）' }).first();
    await expect(official).toBeVisible({ timeout: 15_000 });
    await expect(official).toContainText('门票价格 门票0元/人');
    const officialLink = official.locator('a', { hasText: '来源' });
    await expect(officialLink).toHaveAttribute('href', 'https://www.sxhm.com/ticket');

    // 票务平台来的价 → 必须写明"非官方"，并把**票种名**照原样带出来
    const platform = page.locator('[data-testid="price-provenance"]', { hasText: '票务平台价（非官方）' }).first();
    await expect(platform).toBeVisible();
    await expect(platform).toContainText('回民街美食通兑券成人票');
    await expect(platform.locator('a', { hasText: '来源' })).toHaveAttribute(
      'href',
      'https://piao.qunar.com/ticket/detail_1.html',
    );

    // 文旅部名录登记的官方预约入口 → 可点，且指向登记的那个 URL
    const booking = page.getByTestId('official-booking').first();
    await expect(booking).toBeVisible();
    await expect(booking).toHaveAttribute('href', 'https://www.sxhm.com/visit');

    // 供应商实时价（酒店）也要标出来源类型，不能和"官方门票"混为一谈
    await expect(
      page.locator('[data-testid="price-provenance"]', { hasText: '供应商实时价（可核实）' }).first(),
    ).toBeVisible();

    // 留一张图给人工审阅：价格出处这一排在小屏/长票种名下会不会挤坏
    // （截**标签所在的这一排**，不是整页——整页图里这些标签在折叠线以下，等于没证据）
    await official
      .locator('xpath=..')
      .screenshot({ path: '../work/price-provenance.png', animations: 'disabled' });
  });

  // 👑 方案**台账**也要出现在正文里（2026-10-07）
  //    "第 3 天没有晚餐（最近候选 82.6km，宁可不塞）"、"酒店从 46.9km 换到 2.1km"这类事
  //    以前只躺在 JSON 台账/遗留问题里，行程正文上是隐形的 —— 用户最容易漏掉的就是这些。
  test('台账要落到正文：缺餐提醒 / 换店标记 / 官方简介', async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await installMocks(page);
    await page.goto('/');
    await expect(page.getByTestId('begin-plan')).toBeVisible({ timeout: 20_000 });
    await reachDecision(page, {
      referenceText: '乌鲁木齐4天，人均300，住民宿，南山牧场看雪山，晚上夜市吃烧烤大盘鸡，节奏轻松',
    });
    await page.getByTestId('workspace-layer-plan').click();

    // ① 当天的缺餐提醒（正文里就要看到，不能只进"遗留问题"）
    const dayNotice = page.getByTestId('day-notices').first();
    await expect(dayNotice).toBeVisible({ timeout: 15_000 });
    await expect(dayNotice).toContainText('这天没有安排餐饮');
    await expect(dayNotice).toContainText('82.6km');

    // ①' 每天跑多少公里：超预算的那天必须标明（"长途可以接受，但需要标明"）
    //    第 1 天只是"有远郊点"，第 2 天才是"超预算"，两种文案都要能对上
    const day1Notice = page.locator('[data-notice-kind="long_day"]').first();
    await expect(day1Notice).toBeVisible({ timeout: 10_000 });
    await expect(day1Notice).toContainText('约 162km');
    await expect(day1Notice).toContainText('远郊点');

    await page.getByRole('button', { name: 'Day 2', exact: true }).first().click();
    const day2Notice = page.locator('[data-notice-kind="long_day"]').first();
    await expect(day2Notice).toContainText('约 233km');
    await expect(day2Notice).toContainText('预算 176km');
    // 陈旧快照里的"第 2 天 445km"不许冒出来提示用户（后端已按最终路线重算）
    await expect(page.getByTestId('day-notices').filter({ hasText: '445km' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Day 1', exact: true }).first().click();

    // ② 换过酒店/餐馆的节点要标出来，且带"从多远换到多远"
    const hotelAnnotation = page.locator('[data-annotation-kind="lodging_swap"]').first();
    await expect(hotelAnnotation).toBeVisible();
    await expect(hotelAnnotation).toContainText('46.9km');
    await expect(hotelAnnotation).toContainText('2.1km');
    const mealAnnotation = page.locator('[data-annotation-kind="meal_swap"]').first();
    await expect(mealAnnotation).toContainText('31.5km');

    // ③ 官方登记资料（文旅部名录）的地址与简介要出现在地点资料里
    const officialDetail = page.getByTestId('official-detail').first();
    await expect(officialDetail).toBeVisible();
    await expect(officialDetail).toContainText('小寨东路91号');
    await expect(officialDetail).toContainText('馆藏文物 170 余万件');

    // ③' 官方购票渠道（用户给的三个官方源里有两个只走微信小程序）也要看得见
    const miniprogram = page.getByTestId('official-miniprogram').first();
    await expect(miniprogram).toBeVisible();
    await expect(miniprogram).toContainText('微信小程序');
    await expect(miniprogram).toContainText('巴音布鲁克旅游');
    await expect(miniprogram).toContainText('未授权任何第三方');

    await page.getByTestId('day-notices').first().screenshot({ path: '../work/plan-ledgers.png', animations: 'disabled' });
    // 换店标记也留一张（人工审阅：两行标签 + 官方简介在卡片里会不会太挤）
    await page.locator('[data-annotation-kind="lodging_swap"]').first().locator('xpath=..')
      .screenshot({ path: '../work/plan-annotations.png', animations: 'disabled' });
  });
});
