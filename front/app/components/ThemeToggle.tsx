'use client';

import { useEffect, useState } from 'react';
import { Sun, Moon } from 'lucide-react';

export default function ThemeToggle() {
  const [isDark, setIsDark] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setIsDark(document.documentElement.classList.contains('dark'));
  }, []);

  const toggle = () => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle('dark', next);
    try {
      localStorage.setItem('theme', next ? 'dark' : 'light');
    } catch {
      // 忽略隐私模式下的存储异常
    }
  };

  if (!mounted) return null;

  return (
    <button
      onClick={toggle}
      aria-label={isDark ? '切换到亮色模式' : '切换到暗色模式'}
      title={isDark ? '切换到亮色模式' : '切换到暗色模式'}
      className="fixed bottom-5 left-5 z-[9999] flex h-11 w-11 items-center justify-center rounded-full border border-slate-200 bg-white/80 shadow-lg backdrop-blur transition-colors hover:border-orange-300 dark:border-slate-700 dark:bg-slate-800"
    >
      {isDark ? (
        <Sun className="h-5 w-5 text-amber-400" />
      ) : (
        <Moon className="h-5 w-5 text-slate-600" />
      )}
    </button>
  );
}