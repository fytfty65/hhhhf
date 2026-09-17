/**
 * 大厅通用纯函数工具（自 ContextualLobby.tsx 拆分出来的叶子逻辑）。
 *
 * 仅搬运，未做任何行为改动；后续批次可继续把同类 helper 搬进来。
 */

// 把协议相对 `//` 或 http 图片统一升级为 https，并对高德图片 CDN 显式禁用 Referer，
// 绕过 store.is.autonavi.com 的防盗链/Referer 校验 —— 这是浏览器端图片“完全不可见”的根因。
export function getCleanPhotoUrl(photoUrl?: string, poiName: string = '', photoIndex: number = 0) {
  if (!photoUrl || typeof photoUrl !== 'string') return '';
  let u = photoUrl.trim();
  if (u.startsWith('//')) u = 'https:' + u;
  else if (u.startsWith('http://')) u = 'https://' + u.slice('http://'.length);
  return u.startsWith('https://') ? u : '';
}
