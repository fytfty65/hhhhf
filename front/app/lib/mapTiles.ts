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

/**
 * 第一备选：Esri 街道图。与卫星底图同一个 host（server.arcgisonline.com），
 * 实测可达性明显好于 OSM —— 2026-09-19 实测：高德 200/0.4s、Esri 200/2.2s、
 * **OSM 8s 超时（连不上）**。之前把 OSM 当唯一备用底图，结果一旦误判高德不可用，
 * 切换过去就是一块白板（"已切换备用地图"但地图不可用）。
 */
export const ESRI_STREET_TILES = [
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
] as const;

/** 最后一级备选。实测在部分网络不可达（超时），只在 Esri 街道图也拿不到时启用。 */
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

// 8s 太紧：页面刚起或机器忙的时候，高德第一块瓦片还没到就被判"不可达"，切到备用底图
// （在 OSM 不通的网络里等于把好底图换成白板）。12s 只影响"何时切换"，不影响首屏。
export const MAP_TILE_TIMEOUT_MS = 12000;
// 切到备用底图后，再给一个观察窗口：这段时间里既没有备用瓦片、也没有高德瓦片，
// 就如实告诉用户"底图取不到（路线与编号仍可读）"，而不是假装备用底图在工作。
export const MAP_FALLBACK_GRACE_MS = 6000;

export function validLngLat(value: unknown): value is [number, number] {
  if (!Array.isArray(value) || value.length < 2) return false;
  const lng = Number(value[0]);
  const lat = Number(value[1]);
  return Number.isFinite(lng) && Number.isFinite(lat) && lng >= -180 && lng <= 180 && lat >= -90 && lat <= 90;
}
