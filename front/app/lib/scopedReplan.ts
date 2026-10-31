// 局部调整（只重算某一天 / 只替换交通住宿餐饮）的纯逻辑。
//
// 为什么需要"事后核对"：前端发出去的是一句中文增量，真正按不按"只改这部分"
// 执行由后端决定。不能因为按钮写的是"只换住宿"就宣称其它安排没动 ——
// 所以新方案回来后逐天比对，把实际改到的位置如实报给用户。

export type ScopedCategory = 'transport' | 'lodging' | 'dining' | 'scenic';

export type ScopedScope =
  | { kind: 'day'; day: number }
  | { kind: 'category'; category: ScopedCategory };

export interface ScopedRouteNode {
  day?: number | string;
  name?: string;
  location?: string;
  type?: string;
  tags?: string[];
  category?: string;
  is_hotel?: boolean;
  transport?: string;
  // 节点报价：超支替代方案要按真实报价排序（字符串形如 "¥400" / "暂无供应商数据"）。
  cost?: number | string | null;
  cost_estimate?: number | string | null;
}

export interface ScopedReport {
  scope: ScopedScope;
  label: string;
  inScopeChanged: number;
  outOfScopeChanged: number;
  outOfScopeNames: string[];
  outOfScopeDays: number[];
  respected: boolean;
  failed?: boolean;
  totalNodes: number;
}

export const SCOPED_CATEGORY_LABELS: Record<ScopedCategory, string> = {
  transport: '交通',
  lodging: '住宿',
  dining: '餐饮',
  scenic: '景点',
};

export function scopedScopeLabel(scope: ScopedScope): string {
  if (scope.kind === 'day') return `第 ${scope.day} 天`;
  return SCOPED_CATEGORY_LABELS[scope.category];
}

/** 发给后端的一句子增量：明确说"只动这部分"，但不替后端保证结果。 */
export function buildScopedInstruction(scope: ScopedScope): string {
  if (scope.kind === 'day') {
    return `只重算第 ${scope.day} 天，其它天的景点、餐饮、住宿与顺序保持原样，不要跟着改`;
  }
  switch (scope.category) {
    case 'transport':
      return '只替换交通方式（跨城与市内交通），景点、住宿、餐饮与天数保持原样';
    case 'lodging':
      return '只替换住宿（每晚住哪里），景点、交通、餐饮与天数保持原样';
    case 'dining':
      return '只替换餐饮安排，景点、住宿、交通与天数保持原样';
    case 'scenic':
      return '只替换景点与活动，住宿、交通、餐饮与天数保持原样';
  }
}

export function nodeDay(node: ScopedRouteNode): number {
  const parsed = Number(node?.day);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : 1;
}

export function nodeName(node: ScopedRouteNode): string {
  return String(node?.name || node?.location || '').trim();
}

/** 把节点归到四类之一；认不出来就归 other，绝不猜。 */
export function nodeCategory(node: ScopedRouteNode): ScopedCategory | 'other' {
  const tags = Array.isArray(node?.tags) ? node.tags.join(' ') : '';
  const type = String(node?.type || node?.category || '');
  const name = nodeName(node);
  const haystack = `${tags} ${type} ${name}`;
  if (node?.is_hotel || /住宿|酒店|民宿|客栈|旅馆/.test(haystack)) return 'lodging';
  if (/寻味|餐饮|美食|小吃|餐厅|食堂|正餐|food|dining/.test(haystack)) return 'dining';
  if (/交通|车站|机场|高铁|地铁|巴士|transport/.test(haystack)) return 'transport';
  if (/景点|景区|博物馆|公园|寺庙|古迹|遗址|文化|地标|古镇|scenic|attraction|museum/.test(haystack)) return 'scenic';
  return 'other';
}

function routeKey(node: ScopedRouteNode): string {
  return `${nodeDay(node)}:${nodeName(node)}`;
}

function multisetDiff(before: string[], after: string[]): { removed: string[]; added: string[]; changed: number } {
  const counts = new Map<string, number>();
  before.forEach((key) => counts.set(key, (counts.get(key) || 0) + 1));
  after.forEach((key) => counts.set(key, (counts.get(key) || 0) - 1));
  const removed: string[] = [];
  const added: string[] = [];
  counts.forEach((delta, key) => {
    for (let i = 0; i < Math.abs(delta); i += 1) {
      if (delta > 0) removed.push(key);
      else added.push(key);
    }
  });
  return { removed, added, changed: Math.max(removed.length, added.length) };
}

function nameFromKey(key: string): string {
  const index = key.indexOf(':');
  return index >= 0 ? key.slice(index + 1) : key;
}

function uniqueDays(nodes: ScopedRouteNode[]): number[] {
  return Array.from(new Set(nodes.map(nodeDay))).sort((a, b) => a - b);
}

/**
 * 比对新旧方案，回答"这次真的只改了用户指定的部分吗"。
 * 没有旧方案可比时不宣称"保持原样"：此时 failed=true，界面按"没拿到结果"处理。
 */
export function summarizeScopedChange(
  scope: ScopedScope,
  before: ScopedRouteNode[],
  after: ScopedRouteNode[],
): ScopedReport {
  const label = scopedScopeLabel(scope);
  const base = {
    scope,
    label,
    inScopeChanged: 0,
    outOfScopeChanged: 0,
    outOfScopeNames: [] as string[],
    outOfScopeDays: [] as number[],
    respected: false,
    totalNodes: Array.isArray(after) ? after.length : 0,
  };
  if (!Array.isArray(after) || after.length === 0) return { ...base, failed: true };
  if (!Array.isArray(before) || before.length === 0) return { ...base, failed: true };

  if (scope.kind === 'day') {
    const inDay = (node: ScopedRouteNode) => nodeDay(node) === scope.day;
    const inDiff = multisetDiff(
      before.filter(inDay).map(routeKey),
      after.filter(inDay).map(routeKey),
    );
    const otherDays = uniqueDays([...before, ...after]).filter((day) => day !== scope.day);
    const outNames: string[] = [];
    const outDays: number[] = [];
    otherDays.forEach((day) => {
      const diff = multisetDiff(
        before.filter((node) => nodeDay(node) === day).map(routeKey),
        after.filter((node) => nodeDay(node) === day).map(routeKey),
      );
      [...diff.removed, ...diff.added].forEach((key) => {
        outNames.push(nameFromKey(key));
        outDays.push(day);
      });
    });
    return {
      ...base,
      inScopeChanged: inDiff.changed,
      outOfScopeChanged: outNames.length,
      outOfScopeNames: Array.from(new Set(outNames)),
      outOfScopeDays: Array.from(new Set(outDays)).sort((a, b) => a - b),
      respected: outNames.length === 0,
    };
  }

  // 交通不是独立节点，而是每段的 transport 字段：只比较同一天同一节点的交通值。
  if (scope.category === 'transport') {
    const beforeTransport = new Map(before.map((node) => [routeKey(node), String(node?.transport || '').trim()]));
    const afterTransport = new Map(after.map((node) => [routeKey(node), String(node?.transport || '').trim()]));
    let changedTransport = 0;
    afterTransport.forEach((value, key) => {
      if (beforeTransport.has(key) && beforeTransport.get(key) !== value) changedTransport += 1;
    });
    const structure = multisetDiff(Array.from(beforeTransport.keys()), Array.from(afterTransport.keys()));
    const outNames = [...structure.removed, ...structure.added].map(nameFromKey);
    return {
      ...base,
      inScopeChanged: changedTransport,
      outOfScopeChanged: outNames.length,
      outOfScopeNames: Array.from(new Set(outNames)),
      outOfScopeDays: Array.from(new Set(
        [...structure.removed, ...structure.added].map((key) => Number(key.split(':')[0]) || 1),
      )).sort((a, b) => a - b),
      respected: outNames.length === 0,
    };
  }

  const target = scope.category;
  const isTarget = (node: ScopedRouteNode) => nodeCategory(node) === target;
  const inDiff = multisetDiff(
    before.filter(isTarget).map(routeKey),
    after.filter(isTarget).map(routeKey),
  );
  const outDiff = multisetDiff(
    before.filter((node) => !isTarget(node)).map(routeKey),
    after.filter((node) => !isTarget(node)).map(routeKey),
  );
  const outNames = [...outDiff.removed, ...outDiff.added].map(nameFromKey);
  return {
    ...base,
    inScopeChanged: inDiff.changed,
    outOfScopeChanged: outNames.length,
    outOfScopeNames: Array.from(new Set(outNames)),
    outOfScopeDays: Array.from(new Set(
      [...outDiff.removed, ...outDiff.added].map((key) => Number(key.split(':')[0]) || 1),
    )).sort((a, b) => a - b),
    respected: outNames.length === 0,
  };
}

export function describeScopedReport(report: ScopedReport): { tone: 'ok' | 'warn'; text: string } {
  if (report.failed) {
    return { tone: 'warn', text: '这次没有拿到可用的新方案，行程保持原样；可以稍后再试一次。' };
  }
  if (report.respected && report.inScopeChanged === 0) {
    return { tone: 'ok', text: `已按「只改${report.label}」重排，但没有找到可替换的${report.label}安排，行程保持原样。` };
  }
  if (report.respected) {
    return { tone: 'ok', text: `只改了${report.label}：${report.inScopeChanged} 处调整，其它部分没有变。` };
  }
  const names = report.outOfScopeNames.slice(0, 3).join('、');
  const extra = report.outOfScopeNames.length > 3 ? ` 等 ${report.outOfScopeNames.length} 处` : '';
  return {
    tone: 'warn',
    text: `注意：这次除了${report.label}，还改动了${names}${extra}，没有完全按「只改${report.label}」执行；需要的话可以说一句让它们恢复。`,
  };
}
