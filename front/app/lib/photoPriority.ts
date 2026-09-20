/**
 * 节点配图的来源优先级（P3）。
 *
 * 顺序是产品决定，不是随便排的：
 *   1. **我的实拍** —— 你自己拍的，对"这个地方长什么样"最可信；
 *   2. **已审核的公共实拍** —— 别人真拍并经过审核的；
 *   3. **高德官方实景图** —— 官方渠道，但不是"此刻实况"；
 *   4. **卫星影像 / 街道地图** —— 真实地理影像，但**不是实景照片**，必须如实标注；
 *   5. 什么都没有 → 返回空，交给上层显示占位（绝不编图）。
 *
 * 另一条硬规矩：每一种来源都带着**自己的署名**返回，谁也不能冒充谁。
 */

export type PhotoSourceKind = 'my_photo' | 'public_photo' | 'amap_official' | 'imagery' | 'street_map';

export interface PhotoCandidate {
  url: string;
  kind: PhotoSourceKind;
  credit: string;
}

export interface PhotoSourcesInput {
  /** 本机上你自己上传的实拍（data URL） */
  myPhoto?: string;
  /** 服务器上**已审核**的公共实拍 */
  publicPhotos?: { url?: string; uploader?: string }[];
  /** 高德官方实景图 */
  amapPhotos?: string[];
  /** 卫星影像/街道图的兜底（带后端给的 caption） */
  fallback?: { url?: string; kind?: string; caption?: string; attribution?: string } | null;
}

const KIND_BY_FALLBACK: Record<string, PhotoSourceKind> = {
  satellite: 'imagery',
  imagery: 'imagery',
  street_map: 'street_map',
};

/** 按优先级产出候选图（已去重、丢掉空 URL）。 */
export function orderPhotoSources(input: PhotoSourcesInput): PhotoCandidate[] {
  const out: PhotoCandidate[] = [];
  const seen = new Set<string>();
  const push = (url: unknown, kind: PhotoSourceKind, credit: string) => {
    const value = String(url || '').trim();
    if (!value || seen.has(value)) return;
    seen.add(value);
    out.push({ url: value, kind, credit });
  };

  push(input.myPhoto, 'my_photo', '我的实拍 · 仅本机保存');

  (input.publicPhotos || []).forEach((item) => {
    const credit = item?.uploader ? `其他旅行者实拍 · ${item.uploader} · 已审核` : '其他旅行者实拍 · 已审核';
    push(item?.url, 'public_photo', credit);
  });

  (input.amapPhotos || []).forEach((url) => push(url, 'amap_official', '高德官方实景图'));

  const fallback = input.fallback;
  if (fallback?.url) {
    const kind = KIND_BY_FALLBACK[String(fallback.kind || '')] || 'imagery';
    const credit = String(fallback.caption || '位置示意，非实景照片');
    push(fallback.url, kind, credit);
  }

  return out;
}

/** 是不是"真实照片"（决定要不要标注"位置示意"）。 */
export function isRealPhoto(kind: PhotoSourceKind): boolean {
  return kind === 'my_photo' || kind === 'public_photo' || kind === 'amap_official';
}

/** 给用户看的一句话说明（照片类不写"位置示意"，影像类必须写）。 */
export function describeSource(kind: PhotoSourceKind): string {
  switch (kind) {
    case 'my_photo':
      return '我的实拍 · 仅本机保存';
    case 'public_photo':
      return '其他旅行者实拍 · 已审核';
    case 'amap_official':
      return '高德官方实景图';
    case 'imagery':
      return '位置示意：卫星影像（真实地表，非实景照片）';
    case 'street_map':
      return '位置示意：街道地图（非实景照片）';
    default:
      return '';
  }
}
