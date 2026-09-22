import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeRiskNotification, parseRiskTimestamp } from './riskNotifications.ts';

test('risk notification preserves provenance and converts timestamps', () => {
  const result = normalizeRiskNotification({
    id: 'notice-1',
    city: '杭州',
    severity: 'high',
    title: '景区关闭',
    detail: '请确认替代路线',
    source: 'amap',
    estimated: false,
    status: 'unread',
    created_at: '2026-09-22T08:00:00Z',
    observed_at: 1780041600000,
    signal_sources: JSON.stringify({ weather: { provider: 'amap', estimated: false } }),
    replan_proposal: JSON.stringify({ proposal_id: 'risk-1', status: 'pending_confirmation', affected_nodes: ['西湖'] }),
  });
  assert.equal(result?.severity, 'HIGH');
  assert.equal(result?.source, 'amap');
  assert.equal(result?.estimated, false);
  assert.equal(result?.signalSources?.weather?.provider, 'amap');
  assert.equal(result?.observedAt, 1780041600000);
  assert.equal(result?.replan?.proposal_id, 'risk-1');
});

test('invalid notification records are rejected without inventing identity', () => {
  assert.equal(normalizeRiskNotification({ title: 'missing id' }), null);
  assert.equal(parseRiskTimestamp('not-a-date', 42), 42);
  assert.equal(parseRiskTimestamp(1780041600), 1780041600000);
});
