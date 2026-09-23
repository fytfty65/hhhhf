import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import path from 'node:path';

import nextConfig from '../../next.config.js';

/**
 * FCM Web Push 的 CSP / 路径契约（2026-09-19，B 方案上线时加）。
 *
 * 为什么单独立一条测试：本项目已经被 CSP 白名单坑过两次 ——
 *   1. 地图瓦片域名只在 img-src、不在 connect-src（MapLibre 用 fetch 取瓦片）→ 地图全白；
 *   2. 高德实景图 CDN（aos-comment.amap.com）没进 img-src → 照片全被拦、只剩占位。
 * FCM 是同一类问题：`getToken()` 要连 `fcmregistrations` 与 `firebaseinstallations`，
 * `firebase-messaging-sw.js` 要用 importScripts 拉 gstatic 的 compat SDK。
 * 少任何一个域名，表现都是"用户授权成功、但永远拿不到 token / 收不到推送"，极难排查。
 */

async function cspHeader(): Promise<string> {
  const groups = await nextConfig.headers();
  const group = groups.find((entry: any) => entry.source === '/:path*') ?? groups[0];
  const header = group.headers.find((item: any) => item.key === 'Content-Security-Policy');
  return String(header?.value ?? '');
}

function directive(csp: string, name: string): string {
  const found = csp
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(name + ' '));
  assert.ok(found, `CSP 缺少 ${name}`);
  return found as string;
}

const REQUIRED_PUSH_HOSTS = [
  'https://fcmregistrations.googleapis.com',
  'https://firebaseinstallations.googleapis.com',
  'https://www.gstatic.com',
];

test('FCM 必需域名出现在 connect-src（否则 getToken 静默失败）', async () => {
  const connectSrc = directive(await cspHeader(), 'connect-src');
  for (const host of REQUIRED_PUSH_HOSTS) {
    assert.ok(connectSrc.includes(host), `connect-src 缺少 ${host} → 拿不到推送 token`);
  }
});

test('Service Worker 放在 FCM 要求的路径上（文件名不能改）', () => {
  // FCM 硬性要求：站点根作用域的 /firebase-messaging-sw.js
  const swPath = path.resolve(process.cwd(), 'public', 'firebase-messaging-sw.js');
  assert.ok(existsSync(swPath), `缺少 ${swPath}：文件名与位置是 FCM 的硬性要求`);
});

test('SW 从同源配置路由取密钥（避免"env 填了、SW 忘了改"）', async () => {
  const { readFileSync } = await import('node:fs');
  const swPath = path.resolve(process.cwd(), 'public', 'firebase-messaging-sw.js');
  const source = readFileSync(swPath, 'utf8');
  assert.ok(source.includes('/api/push/config'), 'SW 必须从 /api/push/config 取配置，而不是写死密钥');
  assert.ok(source.includes('notificationclick'), 'SW 需要处理 notificationclick，否则点击通知没反应');
});

test('CSP 仍然锁住脚本与框架来源（不要为了推送把安全基线放开）', async () => {
  const csp = await cspHeader();
  assert.ok(!directive(csp, 'script-src').includes('http://'), 'script-src 不得引入明文 http 来源');
  assert.equal(directive(csp, 'object-src'), "object-src 'none'");
  assert.equal(directive(csp, 'frame-ancestors'), "frame-ancestors 'none'");
  assert.equal(directive(csp, 'base-uri'), "base-uri 'self'");
});
