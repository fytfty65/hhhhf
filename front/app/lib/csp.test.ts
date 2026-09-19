import test from 'node:test';
import assert from 'node:assert/strict';
import nextConfig from '../../next.config.js';

/**
 * CSP 契约测试（2026-09-19 事故）。
 *
 * 现象：地图整块空白、DevTools 网络面板里**一条瓦片请求都没有**、控制台刷
 * "Refused to connect ... violates ... connect-src"，而**在地址栏直接打开同一个瓦片 URL
 * 却能正常显示**。原因是 MapLibre GL 用 fetch/XHR 取栅格瓦片，受 `connect-src` 管；
 * 当初只把瓦片域名加进了 `img-src`，于是请求在出门之前就被 CSP 拦了。
 *
 * 这条测试把"栅格瓦片域名必须同时存在于 img-src 与 connect-src"钉成契约：
 * 以后有人清理白名单时会被立刻拦下，而不是又变成"地图白了但没人知道为什么"。
 */

async function cspHeader(): Promise<string> {
  const groups = await nextConfig.headers();
  const group = groups.find((entry: any) => entry.source === '/:path*') ?? groups[0];
  const header = group.headers.find((item: any) => item.key === 'Content-Security-Policy');
  assert.ok(header, 'next.config.js 必须仍然下发 Content-Security-Policy');
  return String(header.value);
}

function directive(csp: string, name: string): string {
  const found = csp
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(name + ' '));
  assert.ok(found, `CSP 缺少 ${name}`);
  return found;
}

const REQUIRED_TILE_HOSTS = [
  'https://*.is.autonavi.com',
  'https://server.arcgisonline.com',
  'https://tile.openstreetmap.org',
];

test('栅格瓦片域名必须出现在 connect-src（MapLibre 用 fetch 取瓦片）', async () => {
  const connectSrc = directive(await cspHeader(), 'connect-src');
  for (const host of REQUIRED_TILE_HOSTS) {
    assert.ok(connectSrc.includes(host), `connect-src 缺少瓦片域名 ${host} → 地图会被 CSP 拦成白板`);
  }
});

test('栅格瓦片域名也留在 img-src（部分浏览器/回退路径按 <img> 取）', async () => {
  const imgSrc = directive(await cspHeader(), 'img-src');
  for (const host of REQUIRED_TILE_HOSTS) {
    assert.ok(imgSrc.includes(host), `img-src 缺少瓦片域名 ${host}`);
  }
});

test('高德静态地图域名在 img-src 里（节点配图兜底，走 <img>）', async () => {
  const imgSrc = directive(await cspHeader(), 'img-src');
  assert.ok(imgSrc.includes('https://restapi.amap.com'), 'img-src 缺少 https://restapi.amap.com');
});

test('CSP 仍然锁住脚本与框架来源（不要为了修地图把安全基线放开）', async () => {
  const csp = await cspHeader();
  assert.ok(!directive(csp, 'script-src').includes('http://'), 'script-src 不得引入明文 http 来源');
  assert.equal(directive(csp, 'object-src'), "object-src 'none'");
  assert.equal(directive(csp, 'frame-ancestors'), "frame-ancestors 'none'");
  assert.equal(directive(csp, 'base-uri'), "base-uri 'self'");
});
