'use client';

import { useEffect, useState } from 'react';
import { Sun, Moon } from 'lucide-react';

export default function ThemeToggle() {
  const [isDark, setIsDark] = useState(false);
  const [mounted, setMounted] = useState(false);
  // 小屏上输入框获得焦点时先收起悬浮按钮，避免它压住表单和操作区。
  const [typingOnMobile, setTypingOnMobile] = useState(false);

  useEffect(() => {
    setMounted(true);
    setIsDark(document.documentElement.classList.contains('dark'));
  }, []);

  useEffect(() => {
    const isSmallScreen = () => window.matchMedia('(max-width: 639px)').matches;
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target as HTMLElement | null;
      if (isSmallScreen() && target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) {
        setTypingOnMobile(true);
      }
    };
    const onFocusOut = () => setTypingOnMobile(false);
    document.addEventListener('focusin', onFocusIn);
    document.addEventListener('focusout', onFocusOut);
    return () => {
      document.removeEventListener('focusin', onFocusIn);
      document.removeEventListener('focusout', onFocusOut);
    };
  }, []);

  const toggle = () => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle('dark', next);
    document.documentElement.style.colorScheme = next ? 'dark' : 'light';
    try {
      localStorage.setItem('theme', next ? 'dark' : 'light');
    } catch {
      // 忽略隐私模式下的存储异常
    }
  };

  if (!mounted) return null;
  // 收起期间不占位，也不会因为透明仍拦截点击。
  if (typingOnMobile) return null;

  return (
    <button
      onClick={toggle}
      aria-label={isDark ? '切换到亮色模式' : '切换到暗色模式'}
      title={isDark ? '切换到亮色模式' : '切换到暗色模式'}
      className="fixed bottom-4 left-4 z-[9999] flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white/80 shadow-lg backdrop-blur transition-colors hover:border-orange-300 dark:border-slate-700 dark:bg-slate-800 sm:bottom-5 sm:left-5 sm:h-11 sm:w-11"
    >
      {isDark ? (
        <Sun className="h-5 w-5 text-amber-400" />
      ) : (
        <Moon className="h-5 w-5 text-slate-600" />
      )}
    </button>
  );
}
