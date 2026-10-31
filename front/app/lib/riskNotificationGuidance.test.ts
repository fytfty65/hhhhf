import assert from 'node:assert/strict';
import test from 'node:test';
import {
  applySnooze,
  confidenceLabel,
  endOfLocalDay,
  isSnoozed,
  nextActionText,
  parseSnoozeMap,
  pruneSnoozes,
  scopeText,
  severityLabel,
} from './riskNotificationGuidance.ts';

test('风险等级有文本结论，不只靠颜色', () => {
  assert.equal(severityLabel('HIGH'), '高风险');
  assert.equal(severityLabel('MEDIUM'), '需关注');
  assert.equal(severityLabel('LOW'), '低风险');
  // 后端给了没见过的等级时不冒充已知等级
  assert.equal(severityLabel('UNKNOWN'), '待确认');
});

test('低置信风险统一写“建议关注”，实测且非低等级才写实测', () => {
  assert.equal(confidenceLabel({ estimated: true, severity: 'HIGH' }), '估算信号 · 建议关注');
  assert.equal(confidenceLabel({ estimated: false, severity: 'LOW' }), '实测信号 · 建议关注');
  assert.equal(confidenceLabel({ estimated: false, severity: 'HIGH' }), '实测信号');
});

test('现在需要做什么：按真实可用动作给一句话', () => {
  // 有重排提案：先确认，确认前不改方案
  assert.equal(
    nextActionText({ severity: 'HIGH', estimated: false, hasReplan: true, affectedCount: 1 }),
    '先确认这次重排提案：会调整 1 处，确认前方案不变。',
  );
  // 有提案但没给节点：不编节点，仍强调确认前不变
  assert.ok(
    nextActionText({ severity: 'HIGH', estimated: false, hasReplan: true, affectedCount: 0 }).includes('确认前方案不变'),
  );
  // 只有节点、没有提案：先核对，别自动改
  const affectedOnly = nextActionText({ severity: 'HIGH', estimated: false, hasReplan: false, affectedCount: 2 });
  assert.ok(affectedOnly.includes('先核对这些受影响的安排'));
  assert.ok(affectedOnly.includes('不会自动改方案'));
  // 低置信或低等级：建议关注，不催用户改行程
  assert.ok(
    nextActionText({ severity: 'LOW', estimated: true, hasReplan: false, affectedCount: 0 }).includes('建议关注，暂不用改行程'),
  );
  // 普通提醒：不夸大，只说留意后续
  assert.equal(
    nextActionText({ severity: 'HIGH', estimated: false, hasReplan: false, affectedCount: 0 }),
    '暂不用改行程，留意后续更新。',
  );
});

test('影响范围同时列出无需处理的部分，拿不到就不编', () => {
  assert.equal(
    scopeText(['城墙南门'], ['陕西历史博物馆', '回民街小吃']),
    '影响范围：城墙南门；无需处理：其余 2 个安排本次不受影响',
  );
  assert.equal(scopeText(['城墙南门'], []), '影响范围：城墙南门');
  assert.equal(scopeText([], ['陕西历史博物馆']), '无需处理：其余 1 个安排本次不受影响');
  assert.equal(scopeText([], []), '');
});

test('暂不处理只到当天结束，过期记录自动丢弃', () => {
  const now = new Date(2026, 9, 31, 15, 30).getTime();
  const midnight = endOfLocalDay(now);
  assert.ok(midnight > now);
  assert.equal(new Date(midnight).getHours(), 0);

  const map = applySnooze({}, 'notice-1', true, now);
  assert.equal(isSnoozed(map, 'notice-1', now), true);
  // 过了零点就不再压着这条提醒
  assert.equal(isSnoozed(map, 'notice-1', midnight + 1000), false);
  assert.deepEqual(pruneSnoozes(map, midnight + 1000), {});
  // 恢复提醒
  assert.deepEqual(applySnooze(map, 'notice-1', false, now), {});
});

test('暂缓记录解析容错：坏数据当成没暂缓', () => {
  assert.deepEqual(parseSnoozeMap(null), {});
  assert.deepEqual(parseSnoozeMap('not-json'), {});
  assert.deepEqual(parseSnoozeMap('[]'), {});
  assert.deepEqual(parseSnoozeMap(JSON.stringify({ a: 1, b: 'bad' })), { a: 1 });
  assert.deepEqual(parseSnoozeMap(JSON.stringify({ a: 123 })), { a: 123 });
});
