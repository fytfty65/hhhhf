import test from 'node:test';
import assert from 'node:assert/strict';
import {
  validLngLat,
  AMAP_ATTRIBUTION,
  OSM_ATTRIBUTION,
  ESRI_ATTRIBUTION,
  AMAP_NORMAL_TILES,
  ESRI_STREET_TILES,
  OSM_TILES,
  MAP_FALLBACK_GRACE_MS,
  MAP_TILE_TIMEOUT_MS,
} from './mapTiles.ts';

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

test('the normal-map fallback is Esri street map, not OSM', () => {
  // 回归：2026-09-19 实测 OSM 在本机 8s 超时（连不上），把它当唯一备用底图 =
  // "已切换备用地图"但地图是白板。第一备选必须是可达的 Esri 街道图，OSM 只做最后一级。
  assert.ok(ESRI_STREET_TILES.length > 0);
  for (const url of ESRI_STREET_TILES) {
    assert.match(url, /^https:\/\//);
    assert.match(url, /World_Street_Map/);
    assert.match(url, /\{z\}\/\{y\}\/\{x\}/); // ArcGIS 的瓦片顺序是 z/y/x
  }
  for (const url of OSM_TILES) assert.match(url, /\{z\}\/\{x\}\/\{y\}/);
});

test('every tile template carries the three placeholders', () => {
  for (const url of [...AMAP_NORMAL_TILES, ...ESRI_STREET_TILES, ...OSM_TILES]) {
    for (const token of ['{x}', '{y}', '{z}']) assert.ok(url.includes(token), `${url} 缺少 ${token}`);
  }
});

test('fallback timing leaves room for a slow-but-healthy provider', () => {
  // 8s 会把"慢"误判成"不可达"，进而切到可能不通的备用底图；观察窗口必须为正。
  assert.ok(MAP_TILE_TIMEOUT_MS >= 12_000);
  assert.ok(MAP_FALLBACK_GRACE_MS > 0);
});
