import test from 'node:test';
import assert from 'node:assert/strict';
import { nodeAnnotations, dayNotices, officialDetailFor } from './planAnnotations.ts';

// 用真实台账形状（2026-10-07 冷跑出来的 payload）

test('缺餐的那天要给出提醒，并带上"最近候选多远"这个量化原因', () => {
  const plan = {
    meal_topup: {
      actions: [],
      skipped: [
        { day: 3, nearest_km: 82.6, reason: '最近的饭点在 82.6km 外（超过 30km 上限），宁可不塞' },
      ],
    },
  };
  const notices = dayNotices(plan);
  assert.equal(notices[3]?.length, 1);
  assert.equal(notices[3][0].kind, 'meal_missing');
  assert.ok(notices[3][0].text.includes('82.6km'));
  assert.equal(notices[1], undefined, '只有第 3 天缺餐，别给别的天加提醒');
});

test('补进来的饭点：节点与当天都要有说明（这天原本没有餐饮）', () => {
  const plan = {
    meal_topup: {
      actions: [
        { code: 'move_meal', day: 3, node: '伊宁县小波匠家常菜店', from_day: 1, distance_km: 27.7, detail: '' },
      ],
      skipped: [],
    },
  };
  const annotations = nodeAnnotations(plan, '伊宁县小波匠家常菜店');
  assert.equal(annotations.length, 1);
  assert.ok(annotations[0].text.includes('从第 1 天挪来'));
  assert.ok(annotations[0].text.includes('27.7km'));
  assert.equal(dayNotices(plan)[3]?.[0].kind, 'meal_added');
  assert.equal(dayNotices(plan)[3]?.[0].text, '原本没有餐饮，已从第 1 天挪来：伊宁县小波匠家常菜店');
});

test('换过酒店/餐馆要说清"从多远换到多远"', () => {
  const plan = {
    lodging_geo_adjust: {
      actions: [{ day: 2, from: '南河清泉民宿', to: '伊宁市巷往小院民宿', from_km: 46.9, to_km: 2.1 }],
      skipped: [],
    },
    meal_geo_adjust: {
      actions: [{ day: 1, from: '伊宁县小波匠家常菜店', to: '影鹅居餐厅', from_km: 31.5, to_km: 1.9 }],
      skipped: [],
    },
  };
  const hotel = nodeAnnotations(plan, '伊宁市巷往小院民宿');
  assert.equal(hotel[0].kind, 'lodging_swap');
  assert.ok(hotel[0].text.includes('46.9km') && hotel[0].text.includes('2.1km'));
  const meal = nodeAnnotations(plan, '影鹅居餐厅');
  assert.equal(meal[0].kind, 'meal_swap');
  assert.ok(meal[0].text.includes('31.5km'));
  assert.deepEqual(nodeAnnotations(plan, '没换过的餐馆'), []);
});

test('按地理重排换过天的节点要标出来', () => {
  const plan = {
    day_rebalance: { applied: true, before_max_km: 444.5, after_max_km: 231.4, moved: ['百年喀什馆', '阳光公园'] },
  };
  const annotations = nodeAnnotations(plan, '百年喀什馆');
  assert.equal(annotations[0].kind, 'day_rebalanced');
  assert.ok(annotations[0].text.includes('444.5km'));
  assert.deepEqual(nodeAnnotations(plan, '没动过的点'), []);
  // 重排没生效时不该冒出标记
  assert.deepEqual(nodeAnnotations({ day_rebalance: { applied: false, moved: ['百年喀什馆'] } }, '百年喀什馆'), []);
});

test('官方登记资料（文旅部名录）能取到简介/地址/官网/预约', () => {
  const plan = {
    official_profiles: {
      喀什古城: {
        official_name: '喀什地区喀什古城景区',
        official_address: '新疆维吾尔自治区喀什地区喀什市亚瓦格路10号',
        official_intro: '喀什古城是世界上现存规模最大的生土建筑群之一。',
        official_site: '',
        official_booking: 'https://x/ticket',
      },
      空档案: { official_name: '', official_intro: '', official_address: '', official_site: '', official_booking: '' },
    },
  };
  const detail = officialDetailFor(plan, '喀什古城');
  assert.equal(detail?.name, '喀什地区喀什古城景区');
  assert.ok(detail?.intro.includes('生土建筑群'));
  assert.equal(detail?.booking, 'https://x/ticket');
  assert.equal(officialDetailFor(plan, '空档案'), null);
  assert.equal(officialDetailFor(plan, '没有这个点'), null);
  assert.equal(officialDetailFor({}, '喀什古城'), null);
});

test('超预算的那天要标出来（"长途可以接受，但需要标明"）', () => {
  const plan = {
    travel_feasibility: {
      budget_km: 160,
      effective_budget_km: 176,
      days: [
        { day: 1, travel_km: 20.8, budget_km: 160, violations: [], far_nodes: [] },
        {
          day: 3,
          travel_km: 232.7,
          budget_km: 160,
          effective_budget_km: 176,
          violations: ['daily_travel_budget_exceeded'],
          far_nodes: [{ name: '胡杨书院', distance_km: 221.4 }],
        },
      ],
    },
  };
  const notices = dayNotices(plan);
  assert.equal(notices[1], undefined, '没超预算、也没有远郊点的那天不加提醒');
  assert.equal(notices[3]?.length, 1);
  assert.equal(notices[3][0].kind, 'long_day');
  assert.ok(notices[3][0].text.includes('约 233km'));
  assert.ok(notices[3][0].text.includes('预算 176km'), '用"有效预算"（含宽限）比，才是判定超没超的同一个口径');
  assert.ok(notices[3][0].text.includes('胡杨书院'));
});

test('没超预算但有远郊点的那天，只报里程与远点，不说"超预算"', () => {
  const plan = {
    travel_feasibility: {
      effective_budget_km: 176,
      days: [{ day: 1, travel_km: 161.8, violations: [], far_nodes: [{ name: '英吉沙国家湿地公园', distance_km: 66.8 }] }],
    },
  };
  const notice = dayNotices(plan)[1][0];
  assert.equal(notice.kind, 'long_day');
  assert.ok(notice.text.includes('约 162km'));
  assert.ok(notice.text.includes('英吉沙国家湿地公园'));
  assert.ok(!notice.text.includes('超过当天预算'));
});

test('重排后的里程是**重算过**的那份（旧快照要留在 stale_before_recompute 里备查）', () => {
  const plan = {
    travel_feasibility: {
      effective_budget_km: 176,
      recomputed_after_closeout: true,
      stale_before_recompute: { worst_day_km: 445.2, days: [{ day: 2, travel_km: 445.2 }] },
      days: [{ day: 3, travel_km: 232.7, violations: ['daily_travel_budget_exceeded'], far_nodes: [] }],
    },
  };
  const notices = dayNotices(plan);
  assert.equal(notices[2], undefined, '陈旧的"第 2 天 445km"不能再冒出来提示用户');
  assert.ok(notices[3][0].text.includes('233km'));
});

test('没有台账时一切为空（不编造提醒）', () => {
  assert.deepEqual(nodeAnnotations({}, '任意'), []);
  assert.deepEqual(dayNotices({}), {});
  assert.deepEqual(nodeAnnotations(null, '任意'), []);
});
