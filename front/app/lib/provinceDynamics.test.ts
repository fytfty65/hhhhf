import test from 'node:test';
import assert from 'node:assert/strict';
import {
  seasonOf,
  seasonLabel,
  seasonRelevance,
  rankHotspots,
  applyServerRanking,
} from './provinceDynamics.ts';

test('seasonOf 依据月份划分四季', () => {
  const d = (m: number) => new Date(2026, m - 1, 1); // month 下标从 0 起
  assert.equal(seasonOf(d(3)), 'spring');
  assert.equal(seasonOf(d(6)), 'summer');
  assert.equal(seasonOf(d(9)), 'autumn');
  assert.equal(seasonOf(d(12)), 'winter');
  assert.equal(seasonOf(d(1)), 'winter');
});

test('seasonLabel 返回中文标签', () => {
  assert.equal(seasonLabel('winter'), '冬季');
  assert.equal(seasonLabel('summer'), '夏季');
});

test('seasonRelevance 冰雪景点冬季更高', () => {
  const win = seasonRelevance('哈尔滨冰雪大世界', '冰雕雪雕', [], 'winter');
  const sum = seasonRelevance('哈尔滨冰雪大世界', '冰雕雪雕', [], 'summer');
  assert.ok(win > sum);
});

test('seasonRelevance 无季节特征给中性分', () => {
  assert.equal(seasonRelevance('故宫博物院', '皇家宫殿', [], 'winter'), 0.3);
});

test('rankHotspots 零埋点下季节决定排序', () => {
  const ranked = rankHotspots(
    [
      { name: '哈尔滨冰雪大世界', desc: '冰雪' },
      { name: '故宫博物院', desc: '宫殿' },
    ],
    {},
    'winter',
  );
  assert.equal(ranked[0].name, '哈尔滨冰雪大世界');
  assert.ok(ranked[0].score >= 0 && ranked[0].score <= 1);
});

test('rankHotspots 极高埋点热度可反超季节', () => {
  const ranked = rankHotspots(
    [
      { name: '哈尔滨冰雪大世界', desc: '冰雪' },
      { name: '故宫博物院', desc: '宫殿' },
    ],
    { 故宫博物院: { clicks: 1000, likes: 500 } },
    'winter',
  );
  assert.equal(ranked[0].name, '故宫博物院');
});

test('rankHotspots 空输入返回空数组', () => {
  assert.equal(rankHotspots([], {}, 'spring').length, 0);
});

test('applyServerRanking 按服务端顺序重排本地景点', () => {
  const ranked = applyServerRanking(
    [
      { name: 'A', desc: '' },
      { name: 'B', desc: '' },
      { name: 'C', desc: '' },
    ],
    [
      { name: 'C', season_match: 0.8, weight: 0.9, score: 0.84, clicks: 5, likes: 2 },
      { name: 'A', season_match: 0.5, weight: 0.4, score: 0.46, clicks: 1, likes: 0 },
      { name: 'B', season_match: 0.3, weight: 0, score: 0.18, clicks: 0, likes: 0 },
    ],
  );
  assert.deepEqual(ranked.map((r) => r.name), ['C', 'A', 'B']);
});
