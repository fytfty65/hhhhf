import test from 'node:test';
import assert from 'node:assert/strict';
import {
  SEASONS, SEASON_DEMO_INTERVAL_MS, canAnimateSeason, nextSeason,
  restoreSeasonPreferences, seasonFromDate,
} from './season.ts';

const june = new Date(2026, 5, 15);

test('seasonFromDate maps all twelve local calendar months', () => {
  const expected = ['winter', 'winter', 'spring', 'spring', 'spring', 'summer', 'summer', 'summer', 'autumn', 'autumn', 'autumn', 'winter'];
  expected.forEach((season, index) => assert.equal(seasonFromDate(new Date(2026, index, 1)), season));
});

test('demo advances through four seasons and wraps with a 24 second interval', () => {
  SEASONS.forEach((season, index) => assert.equal(nextSeason(season), SEASONS[(index + 1) % 4]));
  assert.equal(SEASON_DEMO_INTERVAL_MS, 24_000);
});

test('missing or malformed storage defaults to current date with motion enabled', () => {
  for (const raw of [null, '', '{broken', 'null', 'false', '42', '[]', '"winter"']) {
    assert.deepEqual(restoreSeasonPreferences(raw, june), { season: 'summer', mode: 'date', motionEnabled: true });
  }
});

test('date mode ignores a stale stored season', () => {
  assert.deepEqual(restoreSeasonPreferences(JSON.stringify({ season: 'winter', mode: 'date', motionEnabled: false }), june), {
    season: 'summer', mode: 'date', motionEnabled: false,
  });
});

test('manual and demo preferences survive serialization', () => {
  for (const mode of ['manual', 'demo'] as const) {
    for (const season of SEASONS) {
      const preferences = { mode, season, motionEnabled: false };
      assert.deepEqual(restoreSeasonPreferences(JSON.stringify(preferences), june), preferences);
    }
  }
});

test('invalid preference fields are independently normalized', () => {
  assert.deepEqual(restoreSeasonPreferences('{"mode":"manual","season":"unknown","motionEnabled":"false"}', june), {
    season: 'summer', mode: 'manual', motionEnabled: true,
  });
  assert.deepEqual(restoreSeasonPreferences('{"mode":"invalid","season":"winter","motionEnabled":false}', june), {
    season: 'summer', mode: 'date', motionEnabled: false,
  });
});

test('animation requires opt-in, no reduced motion, visibility and an idle form', () => {
  for (const enabled of [false, true]) {
    for (const reduced of [false, true]) {
      for (const visible of [false, true]) {
        for (const busy of [false, true]) {
          assert.equal(canAnimateSeason(enabled, reduced, visible, busy), enabled && !reduced && visible && !busy);
        }
      }
    }
  }
});
