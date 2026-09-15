import './globals.css' // 引入全局样式（如 Tailwind）
import ThemeToggle from './components/ThemeToggle'

export const metadata = {
  title: 'OmniRoute Dashboard',
  description: '多智能体旅游路线协商中枢',
}

// 首屏防闪烁：根据 localStorage（手动覆盖）/ 系统偏好设置 .dark 类，并实时跟随系统变化
const themeInitScript = `
(function () {
  var KEY = 'theme';
  function apply() {
    try {
      var t = localStorage.getItem(KEY);
      var isDark = t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.classList.toggle('dark', isDark);
    } catch (e) {}
  }
  apply();
  // 系统主题变化：仅在没有手动覆盖时跟随
  try {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
      if (!localStorage.getItem(KEY)) apply();
    });
  } catch (e) {}
  // 跨标签页手动切换同步
  window.addEventListener('storage', function (e) {
    if (e.key === KEY) apply();
  });
})();
`;

// PWA Service Worker 注册（离线缓存已生成的行程，cache-first）
const swRegisterScript = `
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function (e) {
      console.warn('[PWA] Service Worker 注册失败:', e);
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
      <body>
        {children}
        <ThemeToggle />
        {process.env.NODE_ENV === 'production' && (
          <script dangerouslySetInnerHTML={{ __html: swRegisterScript }} />
        )}
      </body>
    </html>
  )
}