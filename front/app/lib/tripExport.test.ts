import test from 'node:test';
import assert from 'node:assert/strict';
import {
  escapeIcs,
  icsDateKey,
  parseStartDate,
  groupByDay,
  buildIcsCalendar,
  buildDiaryMarkdown,
} from './tripExport.ts';
import type { RouteNode } from './routeEditor.ts';

const nodes: RouteNode[] = [
  { day: 1, name: '大巴扎', time: '09:00', cost: '¥45/人', address: '天山区', desc: '逛巴扎' },
  { day: 1, name: '红山公园', time: '14:00', desc: '看日落' },
  { day: 2, name: '天山天池', time: '10:00', cost: '¥120/人' },
];

test('escapeIcs 转义逗号/分号/换行', () => {
  assert.equal(escapeIcs('a,b;c'), 'a\\,b\\;c');
  assert.equal(escapeIcs('x\ny'), 'x\\ny');
});

test('icsDateKey 输出紧凑日期', () => {
  assert.equal(icsDateKey(new Date(2025, 0, 9)), '20250109');
  assert.equal(icsDateKey(new Date(2026, 7, 31)), '20260831');
});

test('parseStartDate 解析合法日期，非法回退今天', () => {
  assert.equal(icsDateKey(parseStartDate('2026-08-31')), '20260831');
  assert.ok(parseStartDate('bad') instanceof Date);
});

test('groupByDay 按 day 分组并保留节点顺序', () => {
  const g = groupByDay(nodes);
  assert.deepEqual(Array.from(g.keys()), [1, 2]);
  assert.equal(g.get(1)?.length, 2);
  assert.equal(g.get(1)?.[0].name, '大巴扎');
  assert.equal(g.get(2)?.[0].name, '天山天池');
});

test('buildIcsCalendar 生成 VCALENDAR 与逐日 VEVENT', () => {
  const ics = buildIcsCalendar(nodes, { city: '乌鲁木齐', startDate: '2026-08-31' });
  assert.ok(ics.startsWith('BEGIN:VCALENDAR'));
  assert.ok(ics.trimEnd().endsWith('END:VCALENDAR'));
  assert.ok(ics.includes('X-WR-CALNAME:乌鲁木齐'));
  assert.equal((ics.match(/BEGIN:VEVENT/g) || []).length, 3);
  assert.ok(ics.includes('DTSTART;VALUE=DATE:20260831'));
  assert.ok(ics.includes('DTSTART;VALUE=DATE:20260901'));
  assert.ok(ics.includes('SUMMARY:大巴扎'));
  assert.ok(ics.includes('LOCATION:天山区'));
});

test('buildIcsCalendar 输出确定性（两次一致）', () => {
  const a = buildIcsCalendar(nodes, { startDate: '2026-08-31' });
  const b = buildIcsCalendar(nodes, { startDate: '2026-08-31' });
  assert.equal(a, b);
});

test('buildDiaryMarkdown 按天分节输出标题与节点', () => {
  const md = buildDiaryMarkdown(nodes, { city: '乌鲁木齐' });
  assert.ok(md.startsWith('# 乌鲁木齐 旅行日记'));
  assert.ok(md.includes('## Day 1'));
  assert.ok(md.includes('## Day 2'));
  assert.ok(md.includes('- **09:00** · 大巴扎'));
});

test('buildDiaryMarkdown 空行程给出占位', () => {
  const md = buildDiaryMarkdown([]);
  assert.ok(md.includes('（暂无行程节点）'));
});
