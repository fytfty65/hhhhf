import assert from 'node:assert/strict';
import test from 'node:test';
import { isRemoteHost, normalizeBase, resolveApiBase, resolveWsBase } from './gatewayBase.ts';

/**
 * 网关基址推导的回归锁。
 *
 * 为什么值得测：以前 `utils.tsx` 与 `webPush.ts` 各自写死 `:8080`，而且没人验证过
 * "https 页面是否真的用 wss"。远程部署（朋友用 192.168.x.x 打开）全靠这段推导，
 * 一旦推导错了，表现是"页面能开但所有 API/WS 都连不上"，非常难查。
 */

test('显式配置优先：NEXT_PUBLIC_API_BASE 原样生效', () => {
  assert.equal(resolveApiBase('https://api.example.com/', { hostname: 'x' }, '8080'), 'https://api.example.com');
});

test('未配置时按当前页面主机 + 默认端口推导（远程部署的关键）', () => {
  assert.equal(resolveApiBase(undefined, { protocol: 'http:', hostname: '192.168.1.5' }), 'http://192.168.1.5:8080');
});

test('端口可配，且 https 页面保持 https', () => {
  assert.equal(resolveApiBase(undefined, { protocol: 'https:', hostname: 'tour.example.com' }, '9443'), 'https://tour.example.com:9443');
});

test('服务端渲染（没有 location）返回空串，而不是编一个 localhost', () => {
  assert.equal(resolveApiBase(undefined, undefined), '');
});

test('WS 基址永远从 API 基址推导：https → wss', () => {
  assert.equal(resolveWsBase('https://tour.example.com'), 'wss://tour.example.com');
  assert.equal(resolveWsBase('http://192.168.1.5:8080'), 'ws://192.168.1.5:8080');
  assert.equal(resolveWsBase(''), '');
});

test('显式 WS 基址优先（保持既有部署行为）', () => {
  assert.equal(resolveWsBase('http://a:1', 'wss://b:2/'), 'wss://b:2');
});

test('normalizeBase 去尾斜杠且容忍空值', () => {
  assert.equal(normalizeBase('http://a/'), 'http://a');
  assert.equal(normalizeBase('   '), '');
  assert.equal(normalizeBase(null), '');
});

test('isRemoteHost 只用于提示，不参与安全决策', () => {
  assert.equal(isRemoteHost({ hostname: 'localhost' }), false);
  assert.equal(isRemoteHost({ hostname: '127.0.0.1' }), false);
  assert.equal(isRemoteHost({ hostname: '192.168.1.5' }), true);
  assert.equal(isRemoteHost({ hostname: 'tour.example.com' }), true);
  assert.equal(isRemoteHost(undefined), false);
});
