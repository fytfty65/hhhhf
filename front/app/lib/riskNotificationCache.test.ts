import assert from 'node:assert/strict';
import test from 'node:test';
import { enqueueRiskNotificationAction, loadRiskInboxCache, removeRiskNotificationActions, saveRiskInboxCache } from './riskNotificationCache.ts';

function installStorage() {
  const values = new Map<string, string>();
  const previous = (globalThis as any).window;
  (globalThis as any).window = { localStorage: {
    getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  } };
  return () => { (globalThis as any).window = previous; };
}

test('offline risk inbox cache stores items and pending acknowledgement actions', () => {
  const restore = installStorage();
  try {
    saveRiskInboxCache('user-cache', { serverUnread: 1, items: [{ id: 'n1', ts: 10, createdAt: 10, observedAt: 10, type: 'NEW_ALERT', severity: 'HIGH', title: '风险', detail: '详情', estimated: true, status: 'unread' }], pending: [] });
    enqueueRiskNotificationAction('user-cache', { id: 'n1', status: 'acknowledged', queuedAt: 20 });
    const cached = loadRiskInboxCache('user-cache');
    assert.equal(cached?.items[0].title, '风险');
    assert.equal(cached?.pending[0].status, 'acknowledged');
    removeRiskNotificationActions('user-cache', ['n1']);
    assert.equal(loadRiskInboxCache('user-cache')?.pending.length, 0);
  } finally {
    restore();
  }
});
