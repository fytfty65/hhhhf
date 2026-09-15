const CACHE_NAME = 'omniroute-v2';

// install：立即接管（skipWaiting）并预热基础缓存
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(['/', '/manifest.json']))
  );
});

// activate：清理旧版本缓存，避免过期 HTML/chunk 残留
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// 网络优先：始终优先返回最新资源，仅在离线时回退缓存，避免缓存旧 HTML 导致白屏
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});