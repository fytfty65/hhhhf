/**
 * 网关基址的单一推导处（纯函数，可离线测试）。
 *
 * 背景：前端原来把端口写死成 `...:8080`（`app/lib/utils.tsx` 与 `app/lib/webPush.ts` 各写一份），
 * 于是
 *   1) 网关端口一改就要改两处代码；
 *   2) 协议虽然靠 `replace(/^http/, 'ws')` 侥幸处理了，但没人验证过 https 页面是否真的用 wss；
 *   3) `NEXT_PUBLIC_API_BASE` 是**构建期**注入的，远程部署时必须重新构建，容易忘。
 *
 * 现在统一为"运行期优先"：
 *   - 显式配了 `NEXT_PUBLIC_API_BASE` → 用它（保持既有部署行为不变）；
 *   - 否则用当前页面主机 + `NEXT_PUBLIC_GATEWAY_PORT`（默认 8080）推导；
 *   - WebSocket 基址永远由 API 基址推导，保证 https → wss。
 */

export type LocationLike = { protocol?: string; hostname?: string; host?: string } | undefined;

/** 归一化：去掉末尾斜杠；空值返回空串（调用方自行决定回退）。 */
export function normalizeBase(value: string | undefined | null): string {
  return String(value ?? '').trim().replace(/\/+$/, '');
}

/**
 * 推导 API 基址。
 * @param explicit NEXT_PUBLIC_API_BASE（构建期注入，优先）
 * @param location 浏览器 location（服务端渲染时为 undefined）
 * @param gatewayPort NEXT_PUBLIC_GATEWAY_PORT（默认 8080）
 */
export function resolveApiBase(explicit: string | undefined | null, location: LocationLike, gatewayPort?: string | null): string {
  const configured = normalizeBase(explicit);
  if (configured) return configured;
  if (!location || !location.hostname) return '';
  const protocol = String(location.protocol || 'http:').replace(/:$/, '') || 'http';
  const port = String(gatewayPort ?? '').trim() || '8080';
  return `${protocol}://${location.hostname}:${port}`;
}

/**
 * 推导 WebSocket 基址：永远从 API 基址来，https → wss。
 * @param apiBase API 基址
 * @param explicit NEXT_PUBLIC_WS_BASE（显式覆盖，保持既有部署行为）
 */
export function resolveWsBase(apiBase: string, explicit?: string | null): string {
  const configured = normalizeBase(explicit);
  if (configured) return configured;
  const base = normalizeBase(apiBase);
  if (!base) return '';
  if (base.startsWith('https://')) return 'wss://' + base.slice('https://'.length);
  if (base.startsWith('http://')) return 'ws://' + base.slice('http://'.length);
  return base;
}

/** 是否属于"远程部署"（非本机来源）——用于给出部署提示，不参与安全决策。 */
export function isRemoteHost(location: LocationLike): boolean {
  const hostname = String(location?.hostname || '');
  if (!hostname) return false;
  return !['localhost', '127.0.0.1', '::1', '0.0.0.0'].includes(hostname);
}
