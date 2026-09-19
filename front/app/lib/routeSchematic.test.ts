import test from 'node:test';
import assert from 'node:assert/strict';
import { projectSchematic, projectSequence } from './routeSchematic.ts';

const SIZE = { width: 640, height: 420, padding: 48 };

test('没有任何可用坐标时如实返回 false，而不是画一堆点', () => {
  const result = projectSchematic([{ name: 'A' }, { name: 'B', lnglat: 'not-a-coord' }], SIZE);
  assert.equal(result.hasCoordinates, false);
  assert.equal(result.points.length, 0);
  assert.equal(result.polyline, '');
  assert.deepEqual(result.skipped, ['A', 'B']);
});

test('单个点落在画布中心', () => {
  const result = projectSchematic([{ name: '独点', lnglat: [112.45, 34.62] }], SIZE);
  assert.equal(result.hasCoordinates, true);
  assert.equal(result.points.length, 1);
  assert.equal(result.points[0].x, SIZE.width / 2);
  assert.equal(result.points[0].y, SIZE.height / 2);
});

test('两个点被等比铺开且留在 padding 之内', () => {
  const result = projectSchematic(
    [
      { name: '西', lnglat: [110, 30] },
      { name: '东', lnglat: [114, 30] },
    ],
    SIZE,
  );
  const [west, east] = result.points;
  assert.ok(west.x >= SIZE.padding - 0.5, `west.x=${west.x}`);
  assert.ok(east.x <= SIZE.width - SIZE.padding + 0.5, `east.x=${east.x}`);
  assert.ok(west.x < east.x);
  assert.equal(west.y, east.y, '同纬度应画在同一水平线上');
  assert.equal(result.polyline, `${west.x},${west.y} ${east.x},${east.y}`);
});

test('纬度越大越靠上（北在上）', () => {
  const result = projectSchematic(
    [
      { name: '北', lnglat: [112, 36] },
      { name: '南', lnglat: [112, 30] },
    ],
    SIZE,
  );
  assert.ok(result.points[0].y < result.points[1].y);
});

test('缺坐标的节点被跳过，但其余点仍然画出来（并如实列出跳过的）', () => {
  const result = projectSchematic(
    [
      { name: '有点', lnglat: [112, 34] },
      { name: '没点' },
      { name: '也有点', lnglat: [113, 35] },
    ],
    SIZE,
  );
  assert.equal(result.hasCoordinates, true);
  assert.equal(result.plotted, 2);
  assert.deepEqual(
    result.points.map((point) => point.name),
    ['有点', '也有点'],
  );
  assert.deepEqual(result.skipped, ['没点']);
});

test('保持等比：经度跨度大时不会把纬度方向拉满', () => {
  const wide = projectSchematic(
    [
      { name: 'A', lnglat: [100, 30] },
      { name: 'B', lnglat: [140, 31] },
    ],
    SIZE,
  );
  const [a, b] = wide.points;
  // 经度跨度 40 度、纬度跨度 1 度 → 屏幕上 y 方向位移应远小于 x 方向
  assert.ok(Math.abs(b.y - a.y) < Math.abs(b.x - a.x) / 10);
});

test('空输入不抛异常', () => {
  assert.equal(projectSchematic(null, SIZE).hasCoordinates, false);
  assert.equal(projectSchematic([], SIZE).plotted, 0);
});

test('顺序示意图：没有坐标也能按行程顺序铺开（绝不推断地理位置）', () => {
  const result = projectSequence(
    [
      { name: 'A', day: 1 },
      { name: 'B', day: 1 },
      { name: 'C', day: 2 },
    ],
    SIZE,
  );
  assert.equal(result.hasCoordinates, false);
  assert.equal(result.plotted, 3);
  assert.deepEqual(result.byDay, [1, 2]);
  // 同一天从左到右；不同天上下分开（第一天在上）
  assert.ok(result.points[0].x < result.points[1].x);
  assert.equal(result.points[0].y, result.points[1].y);
  assert.ok(result.points[2].y > result.points[0].y);
  // 顺序图的点全部落在 padding 之内
  for (const point of result.points) {
    assert.ok(point.x >= SIZE.padding - 0.5 && point.x <= SIZE.width - SIZE.padding + 0.5);
    assert.ok(point.y >= SIZE.padding - 0.5 && point.y <= SIZE.height - SIZE.padding + 0.5);
  }
});

test('顺序示意图：单节点居中、空输入不抛异常', () => {
  const single = projectSequence([{ name: '独点', day: 1 }], SIZE);
  assert.equal(single.points[0].x, SIZE.width / 2);
  assert.equal(single.points[0].y, SIZE.height / 2);
  assert.equal(projectSequence([], SIZE).plotted, 0);
  assert.equal(projectSequence(null, SIZE).points.length, 0);
});
