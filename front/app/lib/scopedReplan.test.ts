import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildScopedInstruction,
  summarizeScopedChange,
  describeScopedReport,
  nodeCategory,
} from './scopedReplan.ts';

const BASE = [
  { day: 1, name: '陕西历史博物馆', type: '博物馆', transport: '地铁' },
  { day: 1, name: '回民街小吃', type: '餐饮', transport: '步行' },
  { day: 1, name: '钟楼酒店', type: '住宿', is_hotel: true, transport: '打车' },
  { day: 2, name: '城墙南门', type: '文化', transport: '地铁' },
  { day: 2, name: '书院门面馆', type: '餐饮', transport: '步行' },
];

test('只重算第 2 天：只改第 2 天就算守住范围', () => {
  const next = BASE.map((node) => (node.day === 2 && node.name === '城墙南门' ? { ...node, name: '大唐不夜城' } : node));
  const report = summarizeScopedChange({ kind: 'day', day: 2 }, BASE, next);
  assert.equal(report.inScopeChanged, 1);
  assert.equal(report.outOfScopeChanged, 0);
  assert.equal(report.respected, true);
  assert.equal(describeScopedReport(report).text, '只改了第 2 天：1 处调整，其它部分没有变。');
});

test('只重算第 2 天：第 1 天被别人改了就不能说"只改了一天"', () => {
  const next = [
    { day: 1, name: '陕西历史博物馆', type: '博物馆', transport: '地铁' },
    { day: 1, name: '永兴坊', type: '餐饮', transport: '步行' },
    { day: 1, name: '钟楼酒店', type: '住宿', is_hotel: true, transport: '打车' },
    { day: 2, name: '大唐不夜城', type: '文化', transport: '地铁' },
    { day: 2, name: '书院门面馆', type: '餐饮', transport: '步行' },
  ];
  const report = summarizeScopedChange({ kind: 'day', day: 2 }, BASE, next);
  assert.equal(report.respected, false);
  assert.deepEqual(report.outOfScopeNames, ['回民街小吃', '永兴坊']);
  assert.deepEqual(report.outOfScopeDays, [1]);
  const described = describeScopedReport(report);
  assert.equal(described.tone, 'warn');
  assert.ok(described.text.includes('回民街小吃'));
  assert.ok(described.text.includes('只改第 2 天'));
});

test('只换住宿：住宿换一家算守住范围，景点跟着变就要如实提醒', () => {
  const swapped = BASE.map((node) => (node.is_hotel ? { ...node, name: '南门民宿' } : node));
  const ok = summarizeScopedChange({ kind: 'category', category: 'lodging' }, BASE, swapped);
  assert.equal(ok.inScopeChanged, 1);
  assert.equal(ok.respected, true);

  const meddled = swapped.map((node) => (node.name === '陕西历史博物馆' ? { ...node, name: '碑林博物馆' } : node));
  const bad = summarizeScopedChange({ kind: 'category', category: 'lodging' }, BASE, meddled);
  assert.equal(bad.respected, false);
  assert.deepEqual(bad.outOfScopeNames, ['陕西历史博物馆', '碑林博物馆']);
});

test('只换餐饮：换餐厅算范围內，景点不动就算守住', () => {
  const next = BASE.map((node) => (node.name === '回民街小吃' ? { ...node, name: '老孙家泡馍' } : node));
  const report = summarizeScopedChange({ kind: 'category', category: 'dining' }, BASE, next);
  assert.equal(report.inScopeChanged, 1);
  assert.equal(report.respected, true);
});

test('只换交通：只认同一天同一节点的交通方式变化', () => {
  const next = BASE.map((node) => ({ ...node, transport: node.day === 1 ? '地铁+步行' : node.transport }));
  const report = summarizeScopedChange({ kind: 'category', category: 'transport' }, BASE, next);
  assert.equal(report.inScopeChanged, 3);
  assert.equal(report.respected, true);
  assert.ok(describeScopedReport(report).text.includes('交通'));
});

test('只换交通：地点结构被改掉时不能算"只换交通"', () => {
  const next = BASE.filter((node) => node.name !== '城墙南门');
  const report = summarizeScopedChange({ kind: 'category', category: 'transport' }, BASE, next);
  assert.equal(report.respected, false);
  assert.deepEqual(report.outOfScopeNames, ['城墙南门']);
});

test('没拿到新方案时如实说没有结果，不冒充"保持原样"', () => {
  const report = summarizeScopedChange({ kind: 'day', day: 2 }, BASE, []);
  assert.equal(report.failed, true);
  assert.equal(report.respected, false);
  assert.ok(describeScopedReport(report).text.includes('没有拿到可用的新方案'));
});

test('指定部分没有可替换项时，明确说行程没变', () => {
  const report = summarizeScopedChange({ kind: 'category', category: 'lodging' }, [
    { day: 1, name: '陕西历史博物馆', type: '博物馆' },
  ], [
    { day: 1, name: '陕西历史博物馆', type: '博物馆' },
  ]);
  assert.equal(report.respected, true);
  assert.equal(report.inScopeChanged, 0);
  assert.ok(describeScopedReport(report).text.includes('没有找到可替换的住宿'));
});

test('节点分类只按收到的字段判断，认不出来就归 other', () => {
  assert.equal(nodeCategory({ name: '某酒店', tags: ['住宿'] }), 'lodging');
  assert.equal(nodeCategory({ name: '回民街小吃', type: '餐饮' }), 'dining');
  assert.equal(nodeCategory({ name: '陕西历史博物馆' }), 'scenic');
  assert.equal(nodeCategory({ name: '神秘地点' }), 'other');
});

test('增量指令明确点名范围，并要求其它部分保持原样', () => {
  assert.ok(buildScopedInstruction({ kind: 'day', day: 3 }).includes('只重算第 3 天'));
  assert.ok(buildScopedInstruction({ kind: 'category', category: 'dining' }).includes('只替换餐饮'));
  assert.ok(buildScopedInstruction({ kind: 'category', category: 'transport' }).includes('保持原样'));
});
