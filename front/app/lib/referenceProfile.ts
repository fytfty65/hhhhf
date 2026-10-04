/**
 * 参考路线画像（前端侧）：纯逻辑与接口封装。
 *
 * 用户需求：粘贴一条自己喜欢的路线（如小红书帖子）→ 智能体学习成画像 →
 * 当用户没提出更多需求时，按这份偏好生成新路线。
 *
 * 诚实口径（与后端一致，界面上也要这么写）：
 *  - **整篇原文不会被保存**；默认保存"特征 + 出处片段"（片段是为了能向你解释依据），
 *    想要纯净可以勾选"只保存特征"；
 *  - 抽不到的特征会显示为"未识别"，不拿默认值冒充"学到了"；
 *  - 你在当轮诉求里说清楚的要求**永远优先于画像**（画像只在没说明白时才起作用）。
 */

export const REFERENCE_ROUTE_ENDPOINT = '/api/v1/user/preference/reference-route';

export type ReferenceProfile = {
  features?: {
    category_mix?: Record<string, number>;
    pace?: string;
    nodes_per_day?: number | null;
    time_style?: string;
    budget_tier?: string;
    budget_amount?: number | null;
    transport?: string[];
    lodging_style?: string;
    walking_intensity?: string;
  };
  evidence?: Record<string, string>;
  unknown?: string[];
  confidence?: number;
  reference_count?: number;
  source_label?: string;
};

const CATEGORY_LABELS: Record<string, string> = {
  scenic: '自然',
  cultural: '人文',
  food: '美食',
  shopping: '购物',
  nightlife: '夜生活',
  photo: '拍照',
};

const PACE_LABELS: Record<string, string> = { relaxed: '轻松', balanced: '均衡', packed: '紧凑' };
const TIME_LABELS: Record<string, string> = { early_bird: '早起型', night_owl: '夜猫子', balanced: '常规' };
const BUDGET_LABELS: Record<string, string> = { economy: '经济', mid: '舒适', luxury: '高端' };
const LODGING_LABELS: Record<string, string> = { hotel: '酒店', homestay: '民宿', hostel: '青旅' };
const TRANSPORT_LABELS: Record<string, string> = {
  walk: '步行', metro: '地铁/公交', drive: '自驾', charter: '包车', train: '火车', flight: '飞机',
};
const WALKING_LABELS: Record<string, string> = { high: '强度高', medium: '强度中', low: '强度低' };

const UNKNOWN_LABELS: Record<string, string> = {
  category_mix: '内容偏好',
  pace: '节奏',
  nodes_per_day: '节奏与节点数',
  time_style: '时段',
  budget_tier: '消费档',
  transport: '交通',
  lodging_style: '住宿',
  walking_intensity: '步行强度',
};

/** 画像 → 人话摘要（界面直接渲染；空画像返回空数组，不编内容）。 */
export function summarizeProfile(profile: ReferenceProfile | null | undefined): { lines: string[]; unknown: string[] } {
  const features = profile?.features;
  if (!features) return { lines: [], unknown: [] };
  const lines: string[] = [];

  const mix = features.category_mix || {};
  const mixText = Object.entries(mix)
    .filter(([, count]) => Number(count) > 0)
    .sort((left, right) => Number(right[1]) - Number(left[1]))
    .map(([category, count]) => `${CATEGORY_LABELS[category] || category} ${count}`)
    .join(' · ');
  if (mixText) lines.push(`内容偏好：${mixText}`);

  if (features.pace && PACE_LABELS[features.pace]) lines.push(`节奏：${PACE_LABELS[features.pace]}`);
  if (features.time_style && TIME_LABELS[features.time_style]) lines.push(`时段：${TIME_LABELS[features.time_style]}`);
  if (features.budget_tier && BUDGET_LABELS[features.budget_tier]) {
    const amount = typeof features.budget_amount === 'number' ? `（约 ¥${features.budget_amount}）` : '';
    lines.push(`消费档：${BUDGET_LABELS[features.budget_tier]}${amount}`);
  }
  if (features.lodging_style && LODGING_LABELS[features.lodging_style]) {
    lines.push(`住宿：${LODGING_LABELS[features.lodging_style]}`);
  }
  const transport = (features.transport || [])
    .map((mode) => TRANSPORT_LABELS[mode] || mode)
    .filter(Boolean)
    .join(' / ');
  if (transport) lines.push(`交通：${transport}`);
  if (features.walking_intensity && WALKING_LABELS[features.walking_intensity]) {
    lines.push(`步行：${WALKING_LABELS[features.walking_intensity]}`);
  }

  const unknown = (profile?.unknown || []).map((key) => UNKNOWN_LABELS[key] || key);
  return { lines, unknown };
}

/** 置信度百分比（没有就不显示，不假装 100%）。 */
export function confidenceLabel(profile: ReferenceProfile | null | undefined): string | null {
  const value = profile?.confidence;
  if (typeof value !== 'number' || Number.isNaN(value)) return null;
  return `识别覆盖 ${Math.round(value * 100)}%`;
}

/** 构造保存请求体（纯函数：界面与测试共用同一份校验）。 */
export function buildSavePayload(
  text: string,
  sourceLabel: string,
  keepEvidence: boolean,
): { ok: true; payload: Record<string, unknown> } | { ok: false; reason: string } {
  const trimmed = String(text || '').trim();
  if (!trimmed) {
    return { ok: false, reason: '请粘贴参考路线的正文（整篇原文不会被保存）' };
  }
  if ([...trimmed].length > 20000) {
    return { ok: false, reason: '正文过长，请精简到 2 万字以内' };
  }
  return {
    ok: true,
    payload: {
      text: trimmed,
      source_label: String(sourceLabel || '').trim(),
      // 默认保存出处片段（能解释依据）；勾选后只存特征值
      keep_evidence: Boolean(keepEvidence),
    },
  };
}

/** 保存后给用户看的口径说明（与后端 note 保持一致，避免两边说法不同）。 */
export function storageDisclosure(keepEvidence: boolean): string {
  return keepEvidence
    ? '会保存抽出的偏好特征，以及每个特征的出处片段（用于在方案里解释依据）；整篇原文不会被保存。'
    : '只保存抽出的偏好特征，不保存出处片段；整篇原文也不会被保存。';
}
