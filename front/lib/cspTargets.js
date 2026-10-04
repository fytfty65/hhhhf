/**
 * CSP 里"网关来源"的单一推导处（纯函数，CJS：next.config.js 与单测共用）。
 *
 * 为什么需要它：CSP 的 `connect-src` 原本只写死 `http://localhost:8080` 与
 * `ws://127.0.0.1:8080`。本机开发没问题，但**远程测试时**（前端跑在 192.168.x.x:3000，
 * 网关在同一台机器的 8080）浏览器会被 CSP 直接拦掉到网关的请求 ——
 * 现象与 2026-09-19 那次瓦片事故一模一样：网络面板里一条请求都没有、控制台刷
 * "Refused to connect ... violates ... connect-src"。所以这里按部署配置推导，
 * 而不是靠手改白名单。
 *
 * 约定：
 *  - 默认（不配任何环境变量）保持今天的本机行为：localhost / 127.0.0.1 + 8080；
 *  - 配了 `NEXT_PUBLIC_API_BASE`（如 `http://192.168.1.5:8080`）→ 该来源会同时进入
 *    `connect-src`（http 与 ws 两种形式）与 `img-src`（用户实拍图由网关跨源提供）；
 *  - 配了 `NEXT_PUBLIC_GATEWAY_PORT` → 本机三种来源都用这个端口；
 *  - https/wss 会被正确推导（https 页面必须连 wss，写 ws 会被浏览器拦）。
 */

/** 把 http(s) 来源转成对应的 ws(s) 来源。 */
function websocketForm(origin) {
  const trimmed = String(origin || '').trim().replace(/\/+$/, '');
  if (!trimmed) return '';
  if (trimmed.startsWith('https://')) return 'wss://' + trimmed.slice('https://'.length);
  if (trimmed.startsWith('http://')) return 'ws://' + trimmed.slice('http://'.length);
  if (trimmed.startsWith('wss://') || trimmed.startsWith('ws://')) return trimmed;
  return '';
}

/** 从任意来源里取"带端口的主机"（用于本机同主机不同端口的推导）。 */
function hostWithPort(origin, fallbackPort) {
  const trimmed = String(origin || '').trim();
  if (!trimmed) return '';
  try {
    const parsed = new URL(trimmed);
    return parsed.host || `${parsed.hostname}:${fallbackPort}`;
  } catch {
    return '';
  }
}

/**
 * 推导 CSP 需要的网关来源。
 * @param {string} apiBase 显式配置的 API 基址（通常是 NEXT_PUBLIC_API_BASE）
 * @param {string} gatewayPort 本机网关端口（NEXT_PUBLIC_GATEWAY_PORT，默认 8080）
 * @param {string} scheme http | https（页面协议，决定本机来源用 http 还是 https）
 * @returns {{ connect: string[], img: string[] }}
 */
function gatewayTargets(apiBase, gatewayPort, scheme) {
  const port = String(gatewayPort || '8080').trim() || '8080';
  const protocol = String(scheme || 'http').toLowerCase() === 'https' ? 'https' : 'http';
  const connect = new Set();
  const img = new Set();

  const explicit = String(apiBase || '').trim().replace(/\/+$/, '');
  if (explicit) {
    connect.add(explicit);
    const wsForm = websocketForm(explicit);
    if (wsForm) connect.add(wsForm);
    // 用户实拍图由网关提供（跨源 <img>），所以显式来源也要进 img-src
    if (explicit.startsWith('http')) img.add(explicit);
  } else {
    for (const host of ['localhost', '127.0.0.1']) {
      connect.add(`${protocol}://${host}:${port}`);
      connect.add(`${protocol === 'https' ? 'wss' : 'ws'}://${host}:${port}`);
      img.add(`${protocol}://${host}:${port}`);
    }
  }
  return { connect: [...connect], img: [...img] };
}

module.exports = { gatewayTargets, websocketForm, hostWithPort };
