import './globals.css'
import './journey-system.css' // 引入全局样式（如 Tailwind）
import ThemeToggle from './components/ThemeToggle'

export const metadata = {
  title: 'OmniRoute · 与你一起，走向远方',
  description: '结伴旅行路线协同与规划中枢',
}

// 首屏防闪烁：默认浅色（暖纸设计是为浅色打磨的），只有用户手动选择深色才加 .dark。
// 不再跟随系统偏好——系统深色 + 未适配完全的页面会出现大块突兀的浅色/深色拼接。
const themeInitScript = `
(function () {
  var KEY = 'theme';
  function apply() {
    try {
      var isDark = localStorage.getItem(KEY) === 'dark';
      document.documentElement.classList.toggle('dark', isDark);
      // 让原生控件（日期选择器、滚动条、输入框）跟着主题走，避免深色下出现浅色控件。
      document.documentElement.style.colorScheme = isDark ? 'dark' : 'light';
    } catch (e) {}
  }
  apply();
  // 跨标签页手动切换同步
  window.addEventListener('storage', function (e) {
    if (e.key === KEY) apply();
  });
})();
`;

// Service Worker 注册。
// ⚠️ 这里必须注册 **/firebase-messaging-sw.js**，而不是旧的 /sw.js：
//    同一个作用域（/）只能有一个 SW 注册，后注册的会**替换**前一个。之前每次页面加载都注册
//    /sw.js，于是 FCM 的 SW 被顶掉，`getToken()` 拿到的不是它自己的 SW → 表现就是
//    "用户点了开关、看着是开的，但网关一个设备 token 都没收到"（实测 2026-09-19）。
//    这个 SW 同时承担 FCM 推送与离线缓存两类职责。
const swRegisterScript = `
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/firebase-messaging-sw.js').catch(function (e) {
      console.warn('[SW] Service Worker 注册失败:', e);
    });
  });
}
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <meta name="theme-color" content="#f1f5f9" />
        <link rel="manifest" href="/manifest.json" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Rajdhani:wght@500;600;700&display=swap"
          rel="stylesheet"
        />
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="journey-ui">
        {children}
        <ThemeToggle />
        {process.env.NODE_ENV === 'production' && (
          <script dangerouslySetInnerHTML={{ __html: swRegisterScript }} />
        )}
      </body>
    </html>
  )
}