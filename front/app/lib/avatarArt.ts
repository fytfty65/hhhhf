/**
 * 本地生成的确定性头像。
 *
 * 之前用的是 DiceBear `avataaars`（卡通人像）：五官、发型、肤色全部按种子随机，
 * 在 80px 的个人主页头像和 32px 的顶栏里经常出现比例怪、配色和暖纸底色打架的情况。
 * 这里换成本地生成的墨色首字母头像：按种子挑一组低饱和暖色渐变，叠两圈细白环，
 * 中间放衬线首字母。不请求外部域名，风格与整站的暖纸/墨色一致，且同一用户永远同一张。
 */
const PALETTES = [
  { from: '#F0D6C2', to: '#C0673B', ink: '#3A2013' },
  { from: '#E7E3D2', to: '#6E7359', ink: '#2E3326' },
  { from: '#D9E6E4', to: '#41696F', ink: '#1D3A3F' },
  { from: '#EBD9C9', to: '#8C5B42', ink: '#3B2317' },
  { from: '#E4D8E8', to: '#6A5679', ink: '#2D2334' },
  { from: '#EFDCD6', to: '#A4585A', ink: '#3C1F21' },
];

function hashSeed(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i += 1) {
    h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return h;
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/** 取首字：中文取第一个汉字，英文取首字母大写，都没有时用 O。 */
function initialOf(name?: string): string {
  const text = (name || '').trim();
  if (!text) return 'O';
  const first = Array.from(text)[0] || 'O';
  return /[a-z]/i.test(first) ? first.toUpperCase() : first;
}

/**
 * 生成头像的 data URL（可直接塞进 <img src>）。
 * CSP 的 img-src 已经包含 `data:`，改动前请先确认 next.config.js 里仍保留该来源。
 */
export function generatedAvatarUrl(seed: string, name?: string): string {
  const palette = PALETTES[hashSeed(seed || name || 'omniroute') % PALETTES.length];
  const initial = escapeXml(initialOf(name || seed));
  const svg = [
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">',
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">',
    `<stop offset="0" stop-color="${palette.from}"/><stop offset="1" stop-color="${palette.to}"/>`,
    '</linearGradient></defs>',
    '<rect width="100" height="100" fill="url(#g)"/>',
    '<circle cx="50" cy="50" r="43" fill="none" stroke="rgba(255,255,255,0.30)" stroke-width="1"/>',
    '<circle cx="50" cy="50" r="34" fill="none" stroke="rgba(255,255,255,0.55)" stroke-width="1.5"/>',
    '<text x="50" y="50" text-anchor="middle" dominant-baseline="central"',
    ` font-family="Georgia,'Songti SC','Noto Serif SC',serif" font-size="38" font-weight="600"`,
    ` fill="${palette.ink}">${initial}</text>`,
    '</svg>',
  ].join('');
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
