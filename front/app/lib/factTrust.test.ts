// 事实可信度四态的前端测试（P3）。
//
// 重点同样是"不许骗人"：
//   · 没标可信度 ≠ 已核实（老数据必须落到 unknown）；
//   · 认不出来的来源不许猜成官方；
//   · 汇总时"最差的一档"要能选对（给用户一个总体口径）。

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { sourceLabelOf, trustOf, trustSummaryOf } from './factTrust.ts';

test('官网且新鲜 → 已核实', () => {
  const view = trustOf({
    trust: 'verified',
    trust_label: '已核实',
    source_type: 'official_page',
    freshness: 'fresh',
    derived_confidence: 0.9,
  });
  assert.equal(view?.state, 'verified');
  assert.equal(view?.label, '已核实');
  assert.equal(view?.sourceLabel, '官网页面');
  assert.match(view?.action ?? '', /再确认/);
});

test('模型推断 → 尚未核实（哪怕它自称很确定）', () => {
  const view = trustOf({
    trust: 'unknown',
    trust_message: '该说法来自模型推断，不作为事实采用',
    source_type: 'model_inference',
    derived_confidence: 0,
  });
  assert.equal(view?.state, 'unknown');
  assert.equal(view?.label, '尚未核实');
  assert.equal(view?.sourceLabel, '模型推断');
  assert.match(view?.message ?? '', /模型推断/);
});

test('信息较旧仍然展示，但要说清"以官方渠道为准"', () => {
  const view = trustOf({
    trust: 'stale',
    trust_label: '信息较旧',
    trust_message: '观测于 2025-08-01，信息较旧，请以官方渠道为准',
    source_type: 'official_page',
    freshness: 'stale',
    derived_confidence: 0.45,
  });
  assert.equal(view?.state, 'stale');
  assert.equal(view?.label, '信息较旧');
  assert.match(view?.message ?? '', /官方渠道/);
});

test('没有 trust 字段的老数据按 unknown 处理，不能读成已核实', () => {
  assert.equal(trustOf({ name: '某点', cost_estimate: '¥30' }), null);
  assert.equal(trustOf(null), null);
  assert.equal(trustOf('不是对象'), null);
});

test('trust 值写错时落到 unknown，不猜成 verified', () => {
  const view = trustOf({ trust: 'totally-fine', source_type: 'official_page' });
  assert.equal(view?.state, 'unknown');
  assert.equal(view?.label, '尚未核实');
});

test('认不出来的来源一律"无来源"', () => {
  assert.equal(sourceLabelOf('某个没见过的来源'), '无来源');
  assert.equal(sourceLabelOf(''), '无来源');
  assert.equal(sourceLabelOf(null), '无来源');
  // 已知别名要能归一
  assert.equal(sourceLabelOf('official'), '官网页面');
  assert.equal(sourceLabelOf('mct'), '政府/文旅公开页');
  assert.equal(sourceLabelOf('amap'), '高德地图');
});

test('汇总：最差的一档决定总体口径', () => {
  const summary = trustSummaryOf([
    { trust: 'verified', source_type: 'official_page' },
    { trust: 'estimated', source_type: 'amap' },
    { trust: 'unknown', source_type: 'llm' },
  ]);
  assert.equal(summary.counts.verified, 1);
  assert.equal(summary.counts.estimated, 1);
  assert.equal(summary.counts.unknown, 1);
  assert.equal(summary.worst, 'unknown');
});

test('汇总：空集合不给"已核实"', () => {
  const summary = trustSummaryOf([]);
  assert.deepEqual(summary.counts, { verified: 0, estimated: 0, stale: 0, unknown: 0 });
  assert.equal(summary.worst, null);
});
