/**
 * Public, keyless raster tile endpoints used by the map views.
 * Keep several AMap hosts in the array: MapLibre distributes requests across
 * them and a transient host failure does not blank the whole map.
 */
export const AMAP_NORMAL_TILES = [
  'https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}',
  'https://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}',
  'https://webrd03.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}',
  'https://webrd04.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}',
] as const;

export const AMAP_SATELLITE_TILES = [
  'https://webst01.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
  'https://webst02.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
  'https://webst03.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
  'https://webst04.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}',
] as const;

export const AMAP_LABEL_TILES = [
  'https://webst01.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}',
  'https://webst02.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}',
  'https://webst03.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}',
  'https://webst04.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}',
] as const;

/** Keyless fallbacks. The normal fallback is intentionally light so route
 * lines and numbered POIs remain legible when an upstream provider is down. */
export const OSM_TILES = [
  'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png',
  'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png',
] as const;

export const ESRI_SATELLITE_TILES = [
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
] as const;

// MapLibre renders these source attributions through AttributionControl. Keep
// them centralized so every map view presents provider credit consistently,
// including when a fallback layer is active.
export const AMAP_ATTRIBUTION = '© 高德地图';
export const OSM_ATTRIBUTION = '© OpenStreetMap contributors';
export const ESRI_ATTRIBUTION = '© Esri';

export const MAP_TILE_TIMEOUT_MS = 8000;

export function validLngLat(value: unknown): value is [number, number] {
  if (!Array.isArray(value) || value.length < 2) return false;
  const lng = Number(value[0]);
  const lat = Number(value[1]);
  return Number.isFinite(lng) && Number.isFinite(lat) && lng >= -180 && lng <= 180 && lat >= -90 && lat <= 90;
}
