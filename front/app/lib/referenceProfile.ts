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

// ============================================================================
// 截图识别（参考路线的第二种入口）
//
// 用户需求：小红书上的路线常常是一张截图，复制正文很麻烦 —— 直接传图，识别成文字。
//
// 三条口径（前端也要如实说，而不是只写在文档里）：
//  1. **图片不会被服务端保存**：只在识别那一次请求里存在（响应里回 `image_stored:false`）；
//  2. **识别结果先给你核对**：文字会填进输入框、可以改，改完你自己点"学习"才生效 ——
//     识别错一个地名不会被静默学成偏好；
//  3. **识别不了就说识别不了**：不拿一段猜的行程文字冒充识别结果。
// ============================================================================

export const REFERENCE_ROUTE_OCR_ENDPOINT = `${REFERENCE_ROUTE_ENDPOINT}/ocr`;

/** 与 ai-service `core/route_ocr.MAX_IMAGE_BYTES` 对齐的客户端早拦阈值（4MB）。 */
export const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
/** 与 ai-service 支持的格式一致：HEIC 等手机原图先让用户转格式，而不是传上去再失败。 */
export const ALLOWED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

export const OCR_PRIVACY_NOTE =
  '图片只用于这次文字识别，不会保存到服务端；识别出的文字会填到下面的框里，请你核对后再学习。';

export type ImageLike = { name?: string; type?: string; size?: number };

/** 选文件后的本地校验（纯函数：界面与单测共用一套说法）。 */
export function validateImageFile(file: ImageLike | null | undefined): { ok: true } | { ok: false; reason: string } {
  if (!file) return { ok: false, reason: '没有选择文件' };
  const type = String(file.type || '').toLowerCase();
  const size = Number(file.size || 0);
  if (size <= 0) return { ok: false, reason: '这个文件是空的，请换一张截图' };
  if (size > MAX_IMAGE_BYTES) {
    return {
      ok: false,
      reason: `图片 ${Math.round(size / 1024)}KB 超过 ${MAX_IMAGE_BYTES / 1024 / 1024}MB 上限，请压缩或只截取路线部分`,
    };
  }
  if (type === 'image/heic' || type === 'image/heif') {
    return { ok: false, reason: '暂不支持 HEIC（iPhone 原图）：请先截图或转成 PNG/JPEG 再上传' };
  }
  if (!ALLOWED_IMAGE_TYPES.includes(type)) {
    return { ok: false, reason: '请上传 PNG / JPEG / WebP 格式的截图' };
  }
  return { ok: true };
}

/** 识别请求体（base64 data URL 交给后端；不含任何用户身份字段）。 */
export function buildOcrPayload(dataUrl: string, mimeType: string): Record<string, unknown> {
  return { image_base64: String(dataUrl || ''), mime_type: String(mimeType || '') };
}

const OCR_FAILURE_COPY: Record<string, string> = {
  unsupported_mime: '这张图的格式暂不支持（HEIC 等），请转成 PNG/JPEG 后再试，或直接粘贴文字',
  too_large: '图片太大了，请压缩或只截取路线部分，或直接粘贴文字',
  not_base64: '图片读取失败，请重新选择文件，或直接粘贴文字',
  empty_image: '没有读到图片内容，请重新选择文件',
  no_text_in_image: '这张图里没有识别到行程文字（可能是纯风景图或清晰度不足），请换一张截图或粘贴文字',
  empty_transcription: '识别结果为空，请换一张更清晰的截图或粘贴文字',
  vision_not_configured: '服务端还没配置图片识别能力，请改用粘贴文字',
  vision_call_failed: '图片识别服务暂时不可用，请稍后重试或改用粘贴文字',
  ocr_failed: '没能从这张图里读出文字，请改用粘贴文字或换一张截图',
};

/** 失败原因码 → 用户能照着做的下一步（未知原因码也要给出去路，不吞掉）。 */
export function describeOcrFailure(code: string | null | undefined, fallbackMessage?: string): string {
  const key = String(code || '').trim().toLowerCase().replace(/^reference_ocr_/, '');
  const mapped = OCR_FAILURE_COPY[key];
  if (mapped) return mapped;
  const message = String(fallbackMessage || '').trim();
  if (message) return message;
  return '图片识别失败，请改用粘贴文字';
}

/** 从识别响应里取出文字与说明（兼容 data 包裹两种形态；没有文字就是失败）。 */
export function readOcrResult(payload: unknown): { ok: true; text: string; note: string; model: string; warnings: string[] } | { ok: false; reason: string } {
  const outer = payload as { data?: Record<string, unknown>; error?: { code?: string; message?: string } } | null;
  const data = (outer?.data ?? (payload as Record<string, unknown>)) || {};
  const text = String((data as Record<string, unknown>).text || '').trim();
  if (!text) {
    return { ok: false, reason: describeOcrFailure(outer?.error?.code, outer?.error?.message) };
  }
  const warnings = Array.isArray((data as Record<string, unknown>).warnings)
    ? ((data as Record<string, unknown>).warnings as unknown[]).map((item) => String(item))
    : [];
  return {
    ok: true,
    text,
    note: String((data as Record<string, unknown>).note || '识别结果请先核对（可能有错字/漏字），确认后再点学习。'),
    model: String((data as Record<string, unknown>).model || ''),
    warnings,
  };
}
