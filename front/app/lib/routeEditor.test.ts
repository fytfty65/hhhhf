import test from 'node:test';
import assert from 'node:assert/strict';
import {
  parseCostAmount,
  recomputeTimeline,
  moveNodeInDay,
  removeNodeInDay,
  addNodeInDay,
  recomputeBudget,
  makeAdhocNode,
  getCostField,
  type RouteNode,
} from './routeEditor.ts';

function node(name: string, day: number, cost = '¥80/人'): RouteNode {
  return { name, location: name, day, cost, time: '', tags: [], photos: [] };
}

test('parseCostAmount 从多种格式提取金额', () => {
  assert.equal(parseCostAmount('¥45/人'), 45);
  assert.equal(parseCostAmount('预估 ¥120'), 120);
  assert.equal(parseCostAmount('45元'), 45);
  assert.equal(parseCostAmount(''), 0);
  assert.equal(parseCostAmount(undefined), 0);
});

test('getCostField 优先取 cost，回退 cost_estimate', () => {
  assert.equal(getCostField({ cost: '¥100' } as RouteNode), '¥100');
  assert.equal(getCostField({ cost_estimate: '¥60' } as RouteNode), '¥60');
});

test('recomputeTimeline 按天从 09:00 顺序分配时间窗且不改天数', () => {
  const routes = [node('A', 1), node('B', 1), node('C', 2)];
  const out = recomputeTimeline(routes);
  assert.equal(out.length, 3);
  assert.equal(out[0].time, '09:00-11:30');
  assert.equal(out[1].time, '11:30-14:00');
  assert.equal(out[2].time, '09:00-11:30'); // 新的一天从头开始
  assert.deepEqual(out.map((r) => r.day), [1, 1, 2]);
  // 不修改入参
  assert.equal(routes[0].time, '');
});

test('moveNodeInDay 仅调整同一天内顺序并重算时间线', () => {
  const routes = [
    node('A', 1), node('B', 1), node('C', 1),
    node('D', 2), node('E', 2),
  ];
  const out = moveNodeInDay(routes, 1, 0, 2);
  const day1 = out.filter((r) => r.day === 1).map((r) => r.name);
  assert.deepEqual(day1, ['B', 'C', 'A']);
  const day2 = out.filter((r) => r.day === 2).map((r) => r.name);
  assert.deepEqual(day2, ['D', 'E']);
});

test('moveNodeInDay 越界时返回原数组（相同引用）', () => {
  const routes = [node('A', 1), node('B', 1)];
  assert.equal(moveNodeInDay(routes, 1, 0, 99), routes);
});

test('removeNodeInDay 删除节点', () => {
  const routes = [node('A', 1), node('B', 1), node('C', 1)];
  const out = removeNodeInDay(routes, 1, 1);
  assert.deepEqual(out.map((r) => r.name), ['A', 'C']);
});

test('addNodeInDay 默认追加到末尾', () => {
  const routes = [node('A', 1), node('B', 1)];
  const out = addNodeInDay(routes, 1, makeAdhocNode('新景点'));
  assert.deepEqual(out.filter((r) => r.day === 1).map((r) => r.name), ['A', 'B', '新景点']);
  assert.equal(out[2].time, '14:00-16:30');
});

test('recomputeBudget 求和并计算日均，保留原有字段', () => {
  const routes = [node('A', 1, '¥100/人'), node('B', 1, '¥50/人'), node('C', 2, '¥90/人')];
  const out = recomputeBudget(routes, { budget_mode: 'economy' });
  assert.equal(out.budget_mode, 'economy');
  assert.equal(out.total_budget, 240);
  assert.equal(out.daily_avg, 120);
  assert.equal(out.recomputed, true);
});
