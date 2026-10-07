// 价格出处（纯函数）：把后端的价格字段翻成"人话 + 能不能点开看来源"。
//
// 为什么需要（2026-10-07，用户口径"没渲染出来的'已做'等于没做"）：
//   后端这轮把门票接成了三条定向通道（官方文旅名录 / 文旅部 5A 名录 / 票务平台接口），
//   方案里也确实带回了票种名、平台名、来源 URL —— 但前端只显示「预估花销：¥30」，
//   用户根本看不出这是**官方门票**、**票务平台的哪种票**、还是**网摘估算**。
//   价格不带出处时，"¥30"和"¥2999（其实是三日游）"看起来一样可信。
//
// 口径（与后端 `core/price_sources.py` 的来源分级一一对应）：
//   web_page / official / provider / vendor / amap / rollinggo → 可核实（官方页或供应商）
//   platform                                                   → 票务平台价（**非官方**）
//   web / 其它                                                  → 网摘估算
//   unavailable / 空                                            → 暂无供应商数据

export type PriceSourceKind = 'official' | 'supplier' | 'platform' | 'estimate' | 'unknown';

export interface PriceProvenance {
  /** 展示用的价格（已去掉后端拼在字符串里的括号说明，如 "¥110（票务平台价，非官方）" → "¥110"） */
  value: string;
  kind: PriceSourceKind;
  /** 展示用的来源标签，如「官方价（可核实）」「票务平台价（非官方）」 */
  label: string;
  /** 出处细节：票种名（"库尔勒体育公园门票成人票"）或官方页摘要 */
  detail: string;
  /** 可点开的来源链接（官方页 / 票务平台详情页） */
  url: string;
  /** 是否算"可核实" */
  verified: boolean;
  /** 后端自己的来源标记（web_page/platform/web/rollinggo/…），便于排查 */
  rawSource: string;
}

const OFFICIAL_SOURCES = new Set(['web_page', 'official', 'provider', 'vendor', 'amap', 'rollinggo', 'mct']);
const SUPPLIER_LABEL: Record<string, string> = {
  rollinggo: '供应商实时价',
  amap: '高德资料',
  vendor: '供应商价',
  provider: '供应商价',
};

/** "¥110（票务平台价，非官方）" → "¥110"；"暂无供应商数据" 原样返回 */
export function priceValueOf(raw: unknown): string {
  const text = String(raw ?? '').trim();
  if (!text) return '';
  const match = text.match(/^[¥￥]\s*[\d.,]+(?:\s*起)?/);
  if (match) return match[0].replace(/\s+/g, '');
  return text;
}

/** 从节点（或候选）上取价格出处；没有价格信息时返回 null */
export function priceProvenance(node: any): PriceProvenance | null {
  if (!node || typeof node !== 'object') return null;
  const rawCost = node.cost_estimate ?? node.cost ?? '';
  const value = priceValueOf(rawCost);
  const dataSources = (node.data_sources && typeof node.data_sources === 'object') ? node.data_sources : {};
  const rawSource = String(
    node.price_source ?? dataSources.cost_estimate ?? dataSources.cost ?? ''
  ).trim();
  const detail = String(node.price_excerpt ?? '').trim();
  const url = String(node.price_source_url ?? node.cost_source_url ?? '').trim();

  if (!value || /暂无/.test(value)) {
    return null;
  }

  if (OFFICIAL_SOURCES.has(rawSource)) {
    const label = SUPPLIER_LABEL[rawSource] ? `${SUPPLIER_LABEL[rawSource]}（可核实）` : '官方价（可核实）';
    return {
      value, kind: rawSource === 'web_page' || rawSource === 'official' || rawSource === 'mct' ? 'official' : 'supplier',
      label, detail, url, verified: true, rawSource,
    };
  }
  if (rawSource === 'platform') {
    return {
      value, kind: 'platform', label: '票务平台价（非官方）', detail, url, verified: false, rawSource,
    };
  }
  if (rawSource === 'web') {
    return { value, kind: 'estimate', label: '网摘估算', detail, url, verified: false, rawSource };
  }
  // 有价格但没标来源：如实说"来源未标注"，不要默认成可信
  return { value, kind: 'unknown', label: '来源未标注', detail, url, verified: false, rawSource };
}

/** 官方预约入口（文旅部名录登记的景区官网/官方预约平台；按节点名在 official_profiles 里查） */
export function officialProfileOf(plan: any, nodeName: unknown): { site: string; booking: string; name: string } | null {
  const profiles = (plan && typeof plan === 'object' && plan.official_profiles) || null;
  if (!profiles || typeof profiles !== 'object') return null;
  const key = String(nodeName ?? '').trim();
  const entry = (profiles as Record<string, any>)[key];
  if (!entry || typeof entry !== 'object') return null;
  const site = String(entry.official_site ?? '').trim();
  const booking = String(entry.official_booking ?? '').trim();
  if (!site && !booking) return null;
  return { site, booking, name: String(entry.official_name ?? '').trim() };
}

/**
 * 把方案级的 `official_profiles`（按节点名索引）铺到**每个节点**上。
 *
 * 为什么要有这一步：面板是按节点渲染的，而官方档案挂在方案的顶层映射里
 * （`official_profiles: { "喀什古城": {official_site, official_booking, official_name} }`）。
 * 在映射 routes 时铺平，面板就不必再拿到整份方案。
 */
export function officialFieldsFor(nodeName: unknown, plan: any): Record<string, string> {
  const profile = officialProfileOf(plan, nodeName);
  if (!profile) return {};
  return {
    official_name: profile.name,
    official_site: profile.site,
    official_booking: profile.booking,
  };
}
