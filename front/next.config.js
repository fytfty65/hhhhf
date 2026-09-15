/**
 * Next.js configuration.
 *
 * Adds a security-header baseline. Before this change the app shipped no
 * `headers()` at all: no CSP, no `frame-ancestors`, no `nosniff`, no
 * `Referrer-Policy`, so it was clickjackable and any XSS had nothing in the way.
 * That mattered because the bearer token lives in localStorage and the browser
 * talks to the gateway directly.
 *
 * CSP note — read before "fixing" this:
 *   `script-src` includes 'unsafe-inline' because app/layout.tsx injects two
 *   inline bootstrap scripts (theme init before first paint, and service-worker
 *   registration) via dangerouslySetInnerHTML, and the App Router also emits
 *   inline data needed for hydration. Removing 'unsafe-inline' therefore means
 *   routing every request through middleware.ts to mint a per-request nonce and
 *   forcing the route to be dynamic, trading away static rendering for this
 *   app's single entry route. That is a deliberate follow-up, not an oversight:
 *   everything else below is enforced today (no third-party script origins, no
 *   object-src, base-uri locked to self, frame-ancestors none, restricted
 *   connect/frame/img origins). connect-src is broader in development only, so
 *   HMR and the local API keep working.
 */

const isDev = process.env.NODE_ENV !== 'production';

// Map/raster tile hosts, avatar placeholders, globe textures and fonts actually
// referenced by the client.
const IMG_HOSTS = [
  'https://*.is.autonavi.com',
  'https://*.tile.openstreetmap.org',
  'https://tile.openstreetmap.org',
  'https://server.arcgisonline.com',
  'https://api.dicebear.com',
  'https://unpkg.com',
  'https://cdn.jsdelivr.net',
];

// The in-app "real photo" viewer embeds third-party search pages in a sandboxed
// iframe built from user/POI text.
const FRAME_HOSTS = [
  'https://image.baidu.com',
  'https://www.google.com',
  'https://www.bing.com',
];

const API_TARGETS = [
  'http://localhost:8080',
  'http://127.0.0.1:8080',
  'ws://localhost:8080',
  'ws://127.0.0.1:8080',
];

function contentSecurityPolicy() {
  const scriptSrc = isDev
    ? "'self' 'unsafe-inline' 'unsafe-eval' blob:"
    : "'self' 'unsafe-inline' blob:";
  const connectSrc = ["'self'", ...API_TARGETS];
  if (isDev) {
    connectSrc.push('ws://localhost:3001', 'ws://127.0.0.1:3001');
  }

  return [
    "default-src 'self'",
    'script-src ' + scriptSrc,
    // React and Tailwind both emit inline style attributes; Next injects
    // critical CSS inline as well.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    'img-src \'self\' data: blob: ' + IMG_HOSTS.join(' '),
    "font-src 'self' data: https://fonts.gstatic.com",
    'connect-src ' + connectSrc.join(' '),
    'frame-src ' + FRAME_HOSTS.join(' '),
    "media-src 'self' data: blob:",
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    // No part of this product is meant to be embedded elsewhere. This is the
    // modern replacement for X-Frame-Options, sent alongside it for older
    // browsers.
    "frame-ancestors 'none'",
  ]
    .concat(isDev ? [] : ['upgrade-insecure-requests'])
    .join('; ');
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  // 添加 rewrites 配置
  async rewrites() {
    return [
      {
        source: '/amap-traffic',
        destination: 'https://tm.amap.com/trafficengine/mapabc/traffictile',
      },
    ];
  },

  async headers() {
    const securityHeaders = [
      { key: 'Content-Security-Policy', value: contentSecurityPolicy() },
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'X-Frame-Options', value: 'DENY' },
      { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
      {
        key: 'Permissions-Policy',
        // Geolocation stays available: the trip planner uses device location
        // for "near me" planning. Everything unused is denied.
        value: 'camera=(), microphone=(), payment=(), usb=(), geolocation=(self)',
      },
      { key: 'X-DNS-Prefetch-Control', value: 'off' },
    ];

    return [
      {
        source: '/:path*',
        headers: securityHeaders,
      },
      {
        // The service worker must be revalidated so clients pick up changes
        // instead of running a stale cached copy indefinitely.
        source: '/sw.js',
        headers: [
          { key: 'Cache-Control', value: 'no-cache, no-store, must-revalidate' },
          { key: 'Service-Worker-Allowed', value: '/' },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
