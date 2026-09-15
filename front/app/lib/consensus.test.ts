import test from 'node:test';
import assert from 'node:assert/strict';
import { scoreRouteForConsensus } from './consensus.ts';

test('scoreRouteForConsensus 给出可解释的四维评分', () => {
  const score = scoreRouteForConsensus([
    { name: '博物馆', cost: '¥80/人', tags: ['文化'], risk_score: 10 },
    { name: '夜市', cost: '¥40/人', tags: ['美食'], risk_score: 20 },
  ], [{ id: 'u1', interestTags: ['美食'], budgetWeight: 1.2 }]);
  assert.equal(score.dimensions.interest.score, 50);
  assert.ok(score.overall >= 0 && score.overall <= 100);
  assert.equal(score.reasons.length, 4);
});

test('没有价格和风险数据时不产生 NaN', () => {
  const score = scoreRouteForConsensus([{ name: '公园' }]);
  assert.ok(Object.values(score.dimensions).every((dimension) => Number.isFinite(dimension.score)));
});
