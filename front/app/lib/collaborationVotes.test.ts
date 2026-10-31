import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_NODE_NAME_LENGTH,
  normalizeVoteValue,
  normalizeNodeName,
  applyRoomVote,
  voteCountsFor,
  voteCountsByNode,
  pruneRoomVotes,
} from './collaborationVotes.ts';
import type { RoomVoteState } from './collaborationVotes';

test('normalizeVoteValue 只接受 up/down（忽略大小写与空白）', () => {
  assert.equal(normalizeVoteValue('up'), 'up');
  assert.equal(normalizeVoteValue(' DOWN '), 'down');
  assert.equal(normalizeVoteValue(''), null);
  assert.equal(normalizeVoteValue('null'), null);
  assert.equal(normalizeVoteValue(null), null);
  assert.equal(normalizeVoteValue(1), null);
});

test('normalizeNodeName 去空白并按码点截断', () => {
  assert.equal(normalizeNodeName('  故宫  '), '故宫');
  assert.equal(normalizeNodeName(''), '');
  assert.equal(normalizeNodeName(null), '');
  const long = '景'.repeat(MAX_NODE_NAME_LENGTH + 10);
  assert.equal(Array.from(normalizeNodeName(long)).length, MAX_NODE_NAME_LENGTH);
});

test('applyRoomVote 新增、改票与撤销', () => {
  let state: RoomVoteState = {};
  state = applyRoomVote(state, '故宫', 'u1', 'up');
  assert.deepEqual(state, { 故宫: { u1: 'up' } });

  state = applyRoomVote(state, '故宫', 'u2', 'down');
  assert.deepEqual(state, { 故宫: { u1: 'up', u2: 'down' } });

  state = applyRoomVote(state, '故宫', 'u1', 'down');
  assert.deepEqual(state, { 故宫: { u1: 'down', u2: 'down' } });

  state = applyRoomVote(state, '故宫', 'u1', null);
  assert.deepEqual(state, { 故宫: { u2: 'down' } });

  state = applyRoomVote(state, '故宫', 'u2', '');
  assert.deepEqual(state, {});
});

test('applyRoomVote 明确空值撤销，非法票值忽略而不是误删', () => {
  const state: RoomVoteState = { 故宫: { u1: 'up' } };
  assert.equal(applyRoomVote(state, '故宫', 'u1', 'maybe'), state);
  assert.equal(applyRoomVote(state, '故宫', 'u1', undefined), state);
  assert.deepEqual(applyRoomVote(state, '故宫', 'u1', 'null'), {});
  assert.deepEqual(applyRoomVote(state, '故宫', 'u1', ''), {});
  assert.deepEqual(applyRoomVote(state, '故宫', 'u1', null), {});
});

test('applyRoomVote 忽略非法节点名/用户', () => {
  const state: RoomVoteState = { 故宫: { u1: 'up' } };
  assert.equal(applyRoomVote(state, '', 'u1', 'up'), state);
  assert.equal(applyRoomVote(state, '故宫', '', 'up'), state);
});

test('applyRoomVote 不修改传入的状态对象', () => {
  const state: RoomVoteState = { 故宫: { u1: 'up' } };
  const next = applyRoomVote(state, '故宫', 'u2', 'down');
  assert.deepEqual(state, { 故宫: { u1: 'up' } });
  assert.notEqual(next, state);
  assert.notEqual(next['故宫'], state['故宫']);
});

test('voteCountsFor 支持排除自己，避免重复计数', () => {
  const state: RoomVoteState = { 故宫: { me: 'up', u2: 'up', u3: 'down' } };
  assert.deepEqual(voteCountsFor(state, '故宫'), { up: 2, down: 1 });
  assert.deepEqual(voteCountsFor(state, '故宫', 'me'), { up: 1, down: 1 });
  assert.deepEqual(voteCountsFor(state, '不存在'), { up: 0, down: 0 });
  assert.deepEqual(voteCountsFor(state, ''), { up: 0, down: 0 });
});

test('voteCountsByNode 批量统计并跳过无票节点', () => {
  const state: RoomVoteState = {
    故宫: { u1: 'up', u2: 'down' },
    长城: { u1: 'up' },
    天坛: {},
  };
  // 排除 u1 后，长城只剩 0 票，不应出现在结果里（界面也就不渲染空票数）
  assert.deepEqual(voteCountsByNode(state, 'u1'), {
    故宫: { up: 0, down: 1 },
  });
});

test('pruneRoomVotes 清理离开成员的票并删除空节点', () => {
  const state: RoomVoteState = {
    故宫: { u1: 'up', u2: 'down' },
    长城: { u2: 'up' },
  };
  assert.deepEqual(pruneRoomVotes(state, ['u1']), { 故宫: { u1: 'up' } });
});

test('pruneRoomVotes 成员列表为空时不做清理', () => {
  const state: RoomVoteState = { 故宫: { u1: 'up' } };
  assert.equal(pruneRoomVotes(state, []), state);
});
