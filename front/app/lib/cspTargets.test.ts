import test from 'node:test';
import assert from 'node:assert/strict';
import { gatewayTargets, websocketForm } from '../../lib/cspTargets.js';
import nextConfig from '../../next.config.js';

/**
 * CSP 网关来源的契约测试。
 *
 * 事故背景（与 2026-09-19 瓦片事故同型）：CSP 的 connect-src 只放行了 localhost:8080，
 * 于是远程测试时（前端跑在 192.168.x.x:3000、网关同机 8080）浏览器在出门之前就把请求拦掉，
 * 表现为"网络面板一条请求都没有 + 控制台 Refused to connect"，极难定位。
 * 这些测试把"配置里写了哪个网关来源，CSP 就必须放行哪个"钉死。
 */

test('默认（不配环境变量）保持本机开发行为', () => {
  const { connect, img } = gatewayTargets(undefined, undefined, undefined);
  for (const origin of ['http://localhost:8080', 'ws://localhost:8080', 'http://127.0.0.1:8080', 'ws://127.0.0.1:8080']) {
    assert.ok(connect.includes(origin), `connect 缺少 ${origin}：${connect}`);
  }
  assert.ok(img.includes('http://localhost:8080'), `img 缺少网关来源（用户实拍图跨源）：${img}`);
});

test('显式配置的远程网关来源必须同时进 connect 与 img（含 ws 形式）', () => {
  const { connect, img } = gatewayTargets('http://192.168.1.5:8080', undefined, 'http');
  assert.ok(connect.includes('http://192.168.1.5:8080'), `connect 缺少远程来源：${connect}`);
  assert.ok(connect.includes('ws://192.168.1.5:8080'), `connect 缺少 ws 形式：${connect}`);
  assert.ok(img.includes('http://192.168.1.5:8080'), `img 缺少远程来源：${img}`);
  assert.ok(!connect.some((item: string) => item.includes('localhost')), '显式配置时不该再塞本机来源');
});

test('HTTPS 页面必须用 wss（写 ws 会被浏览器拦）', () => {
  const { connect } = gatewayTargets('https://tour.example.com', undefined, 'https');
  assert.ok(connect.includes('https://tour.example.com'));
  assert.ok(connect.includes('wss://tour.example.com'), `缺少 wss 形式：${connect}`);
  assert.ok(!connect.includes('ws://tour.example.com'), 'https 页面不该出现明文 ws');
});

test('网关端口可配，本机三种来源一起变', () => {
  const { connect } = gatewayTargets(undefined, '9090', 'http');
  assert.ok(connect.includes('http://localhost:9090'));
  assert.ok(connect.includes('ws://127.0.0.1:9090'));
  assert.ok(!connect.some((item: string) => item.endsWith(':8080')), `端口没跟着改：${connect}`);
});

test('websocketForm 的边界：空值/尾斜杠/已是 ws', () => {
  assert.equal(websocketForm(''), '');
  assert.equal(websocketForm(null), '');
  assert.equal(websocketForm('http://a.b:8080/'), 'ws://a.b:8080');
  assert.equal(websocketForm('https://a.b/'), 'wss://a.b');
  assert.equal(websocketForm('ws://a.b:1'), 'ws://a.b:1');
  assert.equal(websocketForm('不是来源'), '');
});

test('接线检查：next.config 下发的 CSP 真的包含推导出的网关来源', async () => {
  const groups = await nextConfig.headers();
  const group = groups.find((entry: any) => entry.source === '/:path*') ?? groups[0];
  const csp = String(group.headers.find((item: any) => item.key === 'Content-Security-Policy').value);
  const parts = csp.split(';').map((part: string) => part.trim());
  const connectSrc = parts.find((part: string) => part.startsWith('connect-src '));
  const imgSrc = parts.find((part: string) => part.startsWith('img-src '));
  assert.ok(connectSrc?.includes('http://localhost:8080'), `connect-src 未接线：${connectSrc}`);
  assert.ok(connectSrc?.includes('ws://localhost:8080'), `connect-src 缺 ws：${connectSrc}`);
  assert.ok(imgSrc?.includes('http://localhost:8080'), `img-src 未接线（实拍图会被拦）：${imgSrc}`);
});
