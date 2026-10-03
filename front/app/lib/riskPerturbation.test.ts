import assert from 'node:assert/strict';
import test from 'node:test';
import {
  MIN_PRESERVED_RATIO,
  describePerturbation,
  describeRiskChange,
  normalizeRiskAlert,
} from './riskPerturbation.ts';

/** ai-service `/api/v1/risk/replan-scope` 成功回包的真实形状 */
function okPayload(overrides: Record<string, unknown> = {}) {
  return {
    correlation_id: 'trip-abc',
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
          ...((overrides.candidate as Record<string, unknown>) || {}),
        },
      ],
      failure: overrides.failure,
    },
    events: [{ event_type: 'risk.changed', event_id: 'e1' }],
    note: '仅产出提案与事件；用户确认前不写入新版本。',
  };
}

test('成功的提案：保留率、受影响节点、动作人话与"确认前不动方案"都要给出', () => {
  const view = describePerturbation(okPayload());
  assert.equal(view.status, 'ready');
  assert.equal(view.preservedRatio, 0.75);
  assert.equal(view.keepsEnough, true);
  assert.deepEqual(view.affected, ['day2-spot1']);
  assert.equal(view.preservedCount, 3);
  assert.match(String(view.headline), /保留率 75%/);
  assert.match(String(view.actionLabel), /只在受影响的那几个节点里替换/);
  assert.equal(view.needsConfirmation, true);
  assert.match(view.disclosure, /确认前不会覆盖/);
  assert.equal(view.correlationId, 'trip-abc');
  assert.match(String(view.riskLabel), /严重度 高/);
});

test('保留率低于 70% 必须点明"这不是最小扰动"，不能含糊成"已优化"', () => {
  const view = describePerturbation(okPayload({ candidate: { preserved_ratio: 0.5 } }));
  assert.equal(view.status, 'ready');
  assert.equal(view.keepsEnough, false);
  assert.match(String(view.headline), /低于 70%/);
  assert.match(String(view.headline), /不是最小扰动/);
  assert.match(view.disclosure, /改动幅度不小/);
  assert.equal(MIN_PRESERVED_RATIO, 0.7);
});

test('gateway 标注的失败：如实说没算出来，绝不编一份影响范围', () => {
  const view = describePerturbation({ status: 'unavailable', reason: 'ai_service_unreachable' });
  assert.equal(view.status, 'unavailable');
  assert.deepEqual(view.affected, [], '没算出来就不能有受影响节点清单');
  assert.match(view.headline, /没连上/);
  assert.match(view.disclosure, /不会改动你现在的方案/);
});

test('缺行程上下文时直说是缺行程，而不是"没有风险"', () => {
  const view = describePerturbation({ status: 'needs_user_context', reason: 'trip_not_found', city: '西安' });
  assert.equal(view.status, 'unavailable');
  assert.match(view.headline, /还没找到这个城市的行程/);
});

test('被拒绝时把 HTTP 状态如实带上', () => {
  const view = describePerturbation({ status: 'rejected', http_status: 502 });
  assert.equal(view.status, 'unavailable');
  assert.match(view.headline, /HTTP 502/);
});

test('范围不可判定（degraded）不整条重生成，也不假装有提案', () => {
  const view = describePerturbation({
    correlation_id: 'trip-1',
    status: 'degraded',
    proposal: {
      status: 'degraded',
      candidates: [],
      failure: { code: 'scope_unknown', message: '风险事件没给出影响范围（哪几天/哪些节点）' },
    },
  });
  assert.equal(view.status, 'degraded');
  assert.deepEqual(view.affected, []);
  assert.match(view.headline, /没给出影响范围/);
  assert.equal(view.needsConfirmation, true);
});

test('未收录的动作码原样露出，不硬翻成人话', () => {
  const view = describePerturbation(okPayload({ candidate: { action: 'teleport_everything' } }));
  assert.equal(view.actionCode, 'teleport_everything');
  assert.match(String(view.actionLabel), /teleport_everything/);
});

test('乱七八糟的输入走"没拿到范围"，不抛异常也不猜', () => {
  for (const bad of [null, undefined, '', 42, []]) {
    const view = describePerturbation(bad);
    assert.equal(view.status, 'unavailable');
    assert.deepEqual(view.affected, []);
  }
});

test('变化说明：优先用后端文案，其次按类型/严重度拼，都没有就如实说未提供', () => {
  assert.equal(describeRiskChange({ summary: '景区临时关闭' }), '景区临时关闭');
  assert.match(describeRiskChange({ type: 'typhoon', severity: 'high', affected_days: ['2'] }), /typhoon · 严重度 高 · 影响第 2 天/);
  assert.equal(describeRiskChange({}), '风险发生变化（后端未提供说明）');
  assert.equal(describeRiskChange('大风预警'), '大风预警');
  assert.equal(describeRiskChange(null), '');
});

test('广播解析：没有 minimum_perturbation 就返回 null 区块，不渲染空壳', () => {
  const alert = normalizeRiskAlert({ city: '西安', changes: [{ summary: '景区临时关闭' }] });
  assert.equal(alert?.city, '西安');
  assert.deepEqual(alert?.changes, ['景区临时关闭']);
  assert.equal(alert?.perturbation, null);
});

test('广播解析：完全无内容的 payload 直接忽略', () => {
  assert.equal(normalizeRiskAlert(null), null);
  assert.equal(normalizeRiskAlert({}), null);
  assert.equal(normalizeRiskAlert({ snapshot: { level: 'low' } }), null);
});

test('广播解析：带最小扰动时把两段拼在一起', () => {
  const alert = normalizeRiskAlert({
    city: '西安',
    changes: [{ type: 'typhoon', severity: 'high' }],
    minimum_perturbation: okPayload(),
  });
  assert.equal(alert?.perturbation?.status, 'ready');
  assert.match(String(alert?.changes[0]), /typhoon/);
});
