// 行程编辑纯函数模块：拖拽排序 / 删除 / 添加节点，并自动重算时间线与预算。
// 全部为无副作用纯函数，便于单元测试与复用。

export interface RouteNode {
  day?: number | string;
  name?: string;
  location?: string;
  time?: string;
  time_reason?: string;
  transport?: string;
  tags?: string[];
  cost?: string;
  cost_estimate?: string;
  open_time?: string;
  address?: string;
  rating?: string;
  photos?: string[];
  map_image?: string;
  amap_url?: string;
  desc?: string;
  lnglat?: number[];
  is_hotel?: boolean;
  hotel_candidates?: unknown[];
  split_info?: string;
  [key: string]: unknown;
}

const SLOT_START_MINUTES = 9 * 60; // 每天 09:00 出发
const SLOT_MINUTES = 150; // 每节点约 2.5 小时（游览 + 简餐）

function toDay(n: RouteNode): number {
  return Number(n.day) || 1;
}

function toMinutes(hhmm: string): number {
  const m = /^(\d{1,2}):(\d{2})/.exec(hhmm || '');
  if (!m) return 0;
  return Number(m[1]) * 60 + Number(m[2]);
}

function toHHMM(total: number): string {
  const h = Math.floor(total / 60);
  const mm = total % 60;
  return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

/** 从 "¥45/人" / "45元" / "预估 ¥120" 中提取金额数字 */
export function parseCostAmount(cost?: string): number {
  if (cost == null) return 0;
  const m = String(cost).match(/(\d+(?:\.\d+)?)/);
  return m ? Number(m[1]) : 0;
}

export function getCostField(n: RouteNode): string {
  return String(n.cost ?? n.cost_estimate ?? '');
}

/** 按天分组（保持原数组中的相对顺序） */
export function groupByDay(routes: RouteNode[]): Map<number, RouteNode[]> {
  const map = new Map<number, RouteNode[]>();
  for (const r of routes) {
    const d = toDay(r);
    if (!map.has(d)) map.set(d, []);
    map.get(d)!.push(r);
  }
  return map;
}

/** 将分好组的节点按天展平为单一数组（天数升序，天内顺序保持） */
export function flattenDays(groups: Map<number, RouteNode[]>): RouteNode[] {
  const out: RouteNode[] = [];
  Array.from(groups.keys())
    .sort((a, b) => a - b)
    .forEach((d) => out.push(...groups.get(d)!));
  return out;
}

/** 时间线重算：每天按顺序从 09:00 起分配时间窗（含交通缓冲），返回新数组（不修改入参） */
export function recomputeTimeline(routes: RouteNode[]): RouteNode[] {
  const groups = groupByDay(routes);
  const result = new Map<number, RouteNode[]>();
  groups.forEach((nodes, day) => {
    result.set(
      day,
      nodes.map((n, i) => {
        const start = SLOT_START_MINUTES + i * SLOT_MINUTES;
        const end = start + 150;
        return { ...n, day, time: `${toHHMM(start)}-${toHHMM(end)}` };
      }),
    );
  });
  return flattenDays(result);
}

/** 拖拽排序：将某 day 内 fromIdx 节点移动到 toIdx（同一天内），随后重算时间线 */
export function moveNodeInDay(routes: RouteNode[], day: number, fromIdx: number, toIdx: number): RouteNode[] {
  const groups = groupByDay(routes);
  const nodes = groups.get(day) || [];
  if (fromIdx < 0 || fromIdx >= nodes.length || toIdx < 0 || toIdx >= nodes.length) {
    return routes;
  }
  const [moved] = nodes.splice(fromIdx, 1);
  nodes.splice(toIdx, 0, moved);
  groups.set(day, nodes);
  return recomputeTimeline(flattenDays(groups));
}

/** 删除某 day 内第 idx 个节点，随后重算时间线 */
export function removeNodeInDay(routes: RouteNode[], day: number, idx: number): RouteNode[] {
  const groups = groupByDay(routes);
  const nodes = groups.get(day) || [];
  if (idx < 0 || idx >= nodes.length) return routes;
  nodes.splice(idx, 1);
  groups.set(day, nodes);
  return recomputeTimeline(flattenDays(groups));
}

/** 在某 day 内插入一个节点（默认加到末尾），随后重算时间线 */
export function addNodeInDay(routes: RouteNode[], day: number, node: RouteNode, idx?: number): RouteNode[] {
  const groups = groupByDay(routes);
  const nodes = groups.get(day) || [...routes.filter((r) => toDay(r) === day)];
  const next = { ...node, day };
  if (idx == null || idx < 0 || idx > nodes.length) {
    nodes.push(next);
  } else {
    nodes.splice(idx, 0, next);
  }
  groups.set(day, nodes);
  return recomputeTimeline(flattenDays(groups));
}

/** 从 Node 数组构造一个可添加的新节点 */
export function makeAdhocNode(name: string, opts: Partial<RouteNode> = {}): RouteNode {
  return {
    name: name.trim(),
    location: name.trim(),
    desc: '手动添加的景点',
    tags: ['自定义'],
    cost: '¥0/人',
    time: '',
    transport: '打车/自驾',
    open_time: '暂无供应商数据',
    rating: '4.5',
    photos: [],
    map_image: '',
    amap_url: `https://www.amap.com/search?query=${encodeURIComponent(name)}`,
    ...opts,
  };
}

/** 预算自动重算：按所有节点金额求和，更新 total_budget 与 daily_avg */
export function recomputeBudget(routes: RouteNode[], currentBudget?: Record<string, unknown> | null): Record<string, unknown> {
  const groupTotal = (predicate: (n: RouteNode) => boolean) => {
    let sum = 0;
    for (const n of routes) if (predicate(n)) sum += parseCostAmount(getCostField(n));
    return sum;
  };
  const totalCost = routes.reduce((acc, n) => acc + parseCostAmount(getCostField(n)), 0);
  const days = groupByDay(routes).size || 1;
  const base = currentBudget && typeof currentBudget === 'object' ? currentBudget : {};
  return {
    ...base,
    total_budget: totalCost,
    daily_avg: Math.round(totalCost / days),
    recomputed: true,
    ticket_estimate: groupTotal((n) => !n.is_hotel && !(n.tags?.includes('寻味') || n.tags?.includes('住宿'))),
    dining_estimate: groupTotal((n) => n.tags?.includes('寻味') || n.tags?.includes('餐饮')),
  };
}
