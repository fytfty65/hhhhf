import test from 'node:test';
import assert from 'node:assert/strict';
import {
  PRESENCE_TTL_MS,
  normalizePresence,
  normalizePresenceList,
  mergePresence,
  pruneExpiredPresence,
  presenceForNode,
  presenceAvatarUrl,
} from './collaborationPresence.ts';
import type { NodePresence } from './collaborationPresence';

const mk = (over: Partial<NodePresence> = {}): NodePresence => ({
  userId: 'u1',
  name: '小明',
  role: '美食',
  avatarUrl: '',
  avatarSeed: 'seed-1',
  nodeKey: '故宫',
  updatedAt: 1000,
  ...over,
});

test('normalizePresence 合法记录转为前端字段', () => {
  const p = normalizePresence(
    { user_id: 'u1', node_key: '故宫', name: '小明', role: '美食', avatar_url: '/a.png', avatar_seed: 's' },
    1000,
  );
  assert.equal(p?.userId, 'u1');
  assert.equal(p?.nodeKey, '故宫');
  assert.equal(p?.avatarUrl, '/a.png');
  assert.equal(p?.avatarSeed, 's');
  assert.equal(p?.updatedAt, 1000);
});

test('normalizePresence 支持 camelCase 输入', () => {
  const p = normalizePresence({ userId: 'u2', nodeKey: '长城', avatarUrl: 'http://x/a.png' }, 5);
  assert.equal(p?.userId, 'u2');
  assert.equal(p?.avatarUrl, 'http://x/a.png');
  assert.equal(p?.updatedAt, 5);
});

test('normalizePresence 空 user_id 或 node_key 返回 null', () => {
  assert.equal(normalizePresence({ node_key: '故宫' }), null);
  assert.equal(normalizePresence({ user_id: 'u1' }), null);
  assert.equal(normalizePresence(null), null);
  assert.equal(normalizePresence('x'), null);
});

test('normalizePresenceList 过滤非法项并规范合法项', () => {
  const out = normalizePresenceList(
    [{ user_id: 'u1', node_key: 'A' }, { user_id: 'u2' }, null, { node_key: 'B' }],
    7,
  );
  assert.equal(out.length, 1);
  assert.equal(out[0].userId, 'u1');
  assert.equal(out[0].updatedAt, 7);
});

test('normalizePresenceList 非数组返回空数组', () => {
  assert.deepEqual(normalizePresenceList({}), []);
});

test('mergePresence 按 userId 去重且后到覆盖先到', () => {
  const merged = mergePresence(
    [mk(), mk({ userId: 'u2', nodeKey: '长城' })],
    [mk({ nodeKey: '天坛' })],
  );
  assert.equal(merged.length, 2);
  const byId = new Map(merged.map((p) => [p.userId, p]));
  assert.equal(byId.get('u1')?.nodeKey, '天坛');
  assert.equal(byId.get('u2')?.nodeKey, '长城');
});

test('pruneExpiredPresence 清除超过 TTL 的记录', () => {
  const now = 100_000;
  const fresh = mk({ userId: 'f', updatedAt: now - 1000 });
  const stale = mk({ userId: 's', updatedAt: now - PRESENCE_TTL_MS - 1 });
  const out = pruneExpiredPresence([fresh, stale], now);
  assert.equal(out.length, 1);
  assert.equal(out[0].userId, 'f');
});

test('presenceForNode 返回聚焦指定节点的成员', () => {
  const list = [mk(), mk({ userId: 'u2', nodeKey: '长城' }), mk({ userId: 'u3', nodeKey: '故宫' })];
  assert.deepEqual(presenceForNode(list, '故宫').map((p) => p.userId), ['u1', 'u3']);
});

test('presenceAvatarUrl http/data 直通，相对路径补 base，缺失走 DiceBear', () => {
  assert.equal(presenceAvatarUrl(mk({ avatarUrl: 'http://x/a.png' }), ''), 'http://x/a.png');
  assert.equal(presenceAvatarUrl(mk({ avatarUrl: 'data:image/png;base64,xx' }), ''), 'data:image/png;base64,xx');
  assert.equal(presenceAvatarUrl(mk({ avatarUrl: '/uploads/a.png' }), 'https://api.test'), 'https://api.test/uploads/a.png');
  assert.match(
    presenceAvatarUrl(mk({ avatarUrl: '', avatarSeed: 's1', name: '小明' }), ''),
    /dicebear\.com\/9\.x\/avataaars\/svg\?seed=s1/,
  );
});

test('presenceAvatarUrl 无 seed 时回退到 name', () => {
  const url = presenceAvatarUrl(mk({ avatarUrl: '', avatarSeed: '', name: '小红' }), '');
  assert.match(url, /seed=%E5%B0%8F%E7%BA%A2/);
});
