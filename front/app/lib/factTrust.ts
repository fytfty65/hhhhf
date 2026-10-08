// 事实可信度四态（P3：可插拔的数据新鲜度）
//
// 背景：后端 `core/fact_trust.py` 给每条事实打了 `trust`（verified/estimated/stale/unknown）、
// `source_type`、`freshness`。前端要是只会显示"¥30"，用户还是分不清
// "官网写的价"和"模型编的价"。这里把四态翻成**用户能读懂的一句话**，并给出该做什么。
//
// 与后端保持同一套口径（改名必须两边一起改，测试会钉住）：
//   verified  已核实      官方/政府/票务页，且新鲜
//   estimated 估算        按公开规则推算，或有出处但没记观测时间
//   stale     信息较旧    有出处但超出保质期 —— **仍然展示**，但要说清"旧了"
//   unknown   尚未核实    没来源 / 模型推断 / 压根没有这条事实

export type TrustState = 'verified' | 'estimated' | 'stale' | 'unknown';

export type TrustView = {
  state: TrustState;
  /** 用户可见标签：已核实 / 估算 / 信息较旧 / 尚未核实 */
  label: string;
  /** 该做什么：例如"请以官方渠道为准" */
  action: string;
  /** 具体说明（可能带观测日期或"模型推断不可采信"） */
  message: string;
  /** 来源类型的中文名（官网页面 / 政府文旅公开页 / 模型推断…） */
  sourceLabel: string;
  freshness: string;
  confidence: number;
};

const LABELS: Record<TrustState, string> = {
  verified: '已核实',
  estimated: '估算',
  stale: '信息较旧',
  unknown: '尚未核实',
};

export const TRUST_STYLES: Record<TrustState, string> = {
  verified: 'bg-emerald-50 text-emerald-700 border-emerald-200/80',
  estimated: 'bg-amber-50 text-amber-700 border-amber-200/80',
  stale: 'bg-orange-50 text-orange-700 border-orange-200/80',
  unknown: 'bg-rose-50 text-rose-700 border-rose-200/80',
};

const SOURCE_LABELS: Record<string, string> = {
  official_page: '官网页面',
  government: '政府/文旅公开页',
  official_wechat: '官方公众号',
  ticketing: '官方票务平台',
  poi_provider: 'POI 供应商',
  amap: '高德地图',
  rollinggo: '供应商实时报价',
  knowledge_base: '项目内可信知识库',
  platform: '票务平台（非官方）',
  web: '网摘',
  model_inference: '模型推断',
  none: '无来源',
};

// 认不出来的来源就是"无来源"，绝不猜成官方
const SOURCE_ALIASES: Record<string, string> = {
  official: 'official_page',
  official_site: 'official_page',
  official_site_registry: 'official_page',
  web_page: 'official_page',
  gov: 'government',
  mct: 'government',
  vendor: 'ticketing',
  miniprogram: 'ticketing',
  provider: 'poi_provider',
  kb: 'knowledge_base',
  qunar: 'platform',
  search: 'web',
  llm: 'model_inference',
};

export function sourceLabelOf(raw: unknown): string {
  const text = String(raw ?? '').trim().toLowerCase();
  if (!text) return SOURCE_LABELS.none;
  const normalized = SOURCE_ALIASES[text] || text;
  return SOURCE_LABELS[normalized] || SOURCE_LABELS.none;
}

function normalizeState(raw: unknown): TrustState {
  const text = String(raw ?? '').trim().toLowerCase();
  if (text === 'verified' || text === 'estimated' || text === 'stale' || text === 'unknown') {
    return text as TrustState;
  }
  return 'unknown';
}

function actionFor(state: TrustState): string {
  if (state === 'verified') return '可直接参考；出行前建议再确认一次';
  if (state === 'estimated') return '按公开规则估算，仅供规划参考';
  if (state === 'stale') return '信息较旧，请以官方渠道为准';
  return '当前无法确认，请以官方渠道为准';
}

/**
 * 读一个节点/事实上的可信度。**没有 trust 字段时按 unknown 处理**
 * —— 老数据没标过可信度，"没标"绝不能被读成"已核实"。
 */
export function trustOf(fact: any): TrustView | null {
  if (!fact || typeof fact !== 'object') return null;
  const hasAnyField =
    fact.trust !== undefined ||
    fact.trust_label !== undefined ||
    fact.source_type !== undefined ||
    fact.freshness !== undefined;
  if (!hasAnyField) return null;

  const state = normalizeState(fact.trust);
  const confidence = Number(fact.derived_confidence ?? fact.confidence);
  return {
    state,
    label: String(fact.trust_label || LABELS[state]),
    action: String(fact.trust_action || actionFor(state)),
    message: String(fact.trust_message || ''),
    sourceLabel: String(fact.source_label || sourceLabelOf(fact.source_type ?? fact.source)),
    freshness: String(fact.freshness || 'unknown'),
    confidence: Number.isFinite(confidence) ? confidence : 0,
  };
}

/** 把一天/一份方案里所有节点的可信度汇总（给"信息可信度"总览用）。 */
export function trustSummaryOf(nodes: any): { counts: Record<TrustState, number>; worst: TrustState | null } {
  const counts: Record<TrustState, number> = { verified: 0, estimated: 0, stale: 0, unknown: 0 };
  let worst: TrustState | null = null;
  const order: TrustState[] = ['unknown', 'stale', 'estimated', 'verified'];
  const list = Array.isArray(nodes) ? nodes : [];
  for (const node of list) {
    const view = trustOf(node);
    if (!view) continue;
    counts[view.state] += 1;
    if (worst === null || order.indexOf(view.state) < order.indexOf(worst)) worst = view.state;
  }
  return { counts, worst };
}
