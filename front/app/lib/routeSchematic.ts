/**
 * 纯离线"相对位置示意图"的投影计算（不依赖任何瓦片/网络）。
 *
 * 为什么需要：底图瓦片可能整片取不到（实测某网络下 OSM 超时、沙箱浏览器直接
 * `Failed to fetch (0)`），这时地图区就是一块空白 —— 用户既看不到路线也看不到地点。
 * 我们改成：一张也画不出来时，用经纬度**相对位置**画示意图（不冒充真实地图），
 * 保证"路线与编号仍然可读"这句话是真的。
 */

// 显式带 .ts 后缀：这个模块要被 `node --experimental-strip-types` 直接跑（见 routeSchematic.test.ts），
// 而 Node 的 ESM 解析不做后缀补全（tsconfig 已开 allowImportingTsExtensions）。
import { validLngLat } from './mapTiles.ts';

export type SchematicInput = {
  name?: string;
  lnglat?: unknown;
  day?: number;
};

export type SchematicPoint = {
  x: number;
  y: number;
  index: number;
  name: string;
  day: number;
};

export type SchematicProjection = {
  points: SchematicPoint[];
  polyline: string;
  hasCoordinates: boolean;
  /** 参与绘制的点数量（过滤掉没有坐标的节点之后） */
  plotted: number;
  /** 因为缺坐标被跳过的节点名字（如实告诉用户"这几个画不出来"） */
  skipped: string[];
};

const DEFAULT_WIDTH = 640;
const DEFAULT_HEIGHT = 420;
const DEFAULT_PADDING = 48;

export function projectSchematic(
  nodes: readonly SchematicInput[] | null | undefined,
  options: { width?: number; height?: number; padding?: number } = {},
): SchematicProjection {
  const width = options.width ?? DEFAULT_WIDTH;
  const height = options.height ?? DEFAULT_HEIGHT;
  const padding = options.padding ?? DEFAULT_PADDING;

  const usable: { name: string; day: number; lng: number; lat: number }[] = [];
  const skipped: string[] = [];
  (nodes ?? []).forEach((node, index) => {
    const label = String(node?.name ?? `节点 ${index + 1}`);
    if (validLngLat(node?.lnglat)) {
      const [lng, lat] = node.lnglat as [number, number];
      usable.push({ name: label, day: Number(node?.day) || 1, lng, lat });
    } else {
      skipped.push(label);
    }
  });

  if (usable.length === 0) {
    return { points: [], polyline: '', hasCoordinates: false, plotted: 0, skipped };
  }

  const lngs = usable.map((item) => item.lng);
  const lats = usable.map((item) => item.lat);
  const minLng = Math.min(...lngs);
  const maxLng = Math.max(...lngs);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const spanLng = maxLng - minLng;
  const spanLat = maxLat - minLat;
  const availW = Math.max(1, width - padding * 2);
  const availH = Math.max(1, height - padding * 2);

  // 等比缩放（经纬度在示意图尺度上当成平面直角坐标，够用且不会把路线拉扁）
  const scale = Math.min(
    spanLng > 0 ? availW / spanLng : Number.POSITIVE_INFINITY,
    spanLat > 0 ? availH / spanLat : Number.POSITIVE_INFINITY,
  );
  const usableScale = Number.isFinite(scale) ? scale : 1;

  const centerLng = (minLng + maxLng) / 2;
  const centerLat = (minLat + maxLat) / 2;

  const points = usable.map((item, index) => {
    const x = width / 2 + (item.lng - centerLng) * usableScale;
    // 纬度越大越靠北 → 屏幕上越靠上
    const y = height / 2 - (item.lat - centerLat) * usableScale;
    return { x: round(x), y: round(y), index, name: item.name, day: item.day };
  });

  return {
    points,
    polyline: points.map((point) => `${point.x},${point.y}`).join(' '),
    hasCoordinates: true,
    plotted: points.length,
    skipped,
  };
}

function round(value: number): number {
  return Math.round(Math.max(0, Math.min(10_000, value)) * 10) / 10;
}
