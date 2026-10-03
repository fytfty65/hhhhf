/* eslint-disable no-undef */
/**
 * FCM Web Push 的 Service Worker（B 方案）。
 *
 * 关键设计：**你不需要改这个文件**。配置从同源 `/api/push/config` 取（那里读的是前端的
 * `NEXT_PUBLIC_FIREBASE_*` 环境变量），所以"填密钥"只发生在 `.env.local` 里，不会出现
 * "env 填了、SW 里忘了改"这种最难查的问题。
 *
 * 注意：这个文件名是 FCM 硬性要求的（`firebase-messaging-sw.js` 且在站点根作用域），不能改。
 */

// 离线缓存：自旧的 public/sw.js 合并而来（那个文件已停止注册，直接删会永久丢掉这块能力）。
// 只缓存**静态不可变资源**；页面导航与所有 /api 请求一律放行不拦。
const CACHE_NAME = 'omniroute-v2';
const PRECACHE_URLS = ['/manifest.json']; // 故意不预热 '/'：缓存 HTML 会让用户看到旧页面
const STATIC_PREFIXES = ['/_next/static/', '/icon', '/icons/', '/favicon'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .catch(() => undefined) // 预热失败不能挡住 SW 安装：离线能力降级，但推送必须可用
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

// ⚠️ 拦截范围必须严格受限（实测教训）：SW 里 `respondWith` 出去的请求**不受 Playwright
// `page.route` 拦截**，一旦把 /api 纳入，E2E 的 mock 会被绕过、页面拿不到数据而整块不渲染
// （我们就是这样把 provider-quality 那条用例跑红的）。所以：
//   非 GET / 跨源 / `/api` / 页面导航 → 一律放行；只有静态资源走"网络优先 + 离线回退"。
self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  if (request.mode === 'navigate') return;
  if (!STATIC_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) return;
  event.respondWith(fetch(request).catch(() => caches.match(request)));
});

async function loadConfig() {
  const response = await fetch('/api/push/config', { cache: 'no-store' });
  if (!response.ok) throw new Error('push config unavailable');
  return response.json();
}

try {
  // 用 compat 版：Service Worker 里没有打包器，只能 importScripts
  importScripts('https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js');
  importScripts('https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js');

  loadConfig()
    .then((config) => {
      if (!config || config.enabled !== true) {
        // 未配置就安静退出：前台会给出"尚未配置推送"的人话提示，SW 不报错
        return;
      }
      firebase.initializeApp(config);
      const messaging = firebase.messaging();

      messaging.onBackgroundMessage((payload) => {
        const notification = payload.notification || {};
        const data = payload.data || {};
        self.registration.showNotification(notification.title || '行程有更新', {
          body: notification.body || '',
          icon: notification.icon || '/icon-192.png',
          badge: '/icon-192.png',
          data,
          tag: data.tag || 'omni-risk',
        });
      });
    })
    .catch(() => {
      /* 配置取不到就什么都不做：前台仍能用站内通知 */
    });
} catch (error) {
  // importScripts 失败（离线/被网络策略拦截）不能把 SW 搞崩，否则连站内通知都没了
}

// 点通知 → 打开/聚焦对应页面（data.link 由服务端下发）
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          client.navigate(link);
          return client.focus();
        }
      }
      return self.clients.openWindow(link);
    }),
  );
});
