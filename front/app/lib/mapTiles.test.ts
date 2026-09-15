import test from 'node:test';
import assert from 'node:assert/strict';
import { validLngLat, AMAP_ATTRIBUTION, OSM_ATTRIBUTION, ESRI_ATTRIBUTION } from './mapTiles.ts';

test('validLngLat accepts geographic boundary coordinates', () => {
  assert.equal(validLngLat([-180, -90]), true);
  assert.equal(validLngLat([180, 90]), true);
  assert.equal(validLngLat([0, 0]), true);
});

test('validLngLat rejects malformed and out-of-range coordinates', () => {
  assert.equal(validLngLat(undefined), false);
  assert.equal(validLngLat(['not-a-lng', 'not-a-lat']), false);
  assert.equal(validLngLat([181, 0]), false);
  assert.equal(validLngLat([0, -91]), false);
  assert.equal(validLngLat([Number.NaN, 39.9]), false);
  assert.equal(validLngLat([116.4]), false);
});

test('map provider attribution labels are present for every source family', () => {
  assert.match(AMAP_ATTRIBUTION, /高德/);
  assert.match(OSM_ATTRIBUTION, /OpenStreetMap/);
  assert.match(ESRI_ATTRIBUTION, /Esri/);
});
