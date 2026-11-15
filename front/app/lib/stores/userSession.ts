'use client';

import { useEffect, useRef, useState } from 'react';
import { apiFetch, clearAccessToken, getAuthToken, refreshAccessToken } from '../utils';
import type { UserProfile } from '../../types';

// 用户会话 Store：登录态恢复、令牌续期、资料更新与退出登录。
// 把它从页面组件里拆出来，页面只关心「当前用户是谁」，不关心令牌怎么续、
// localStorage 什么时候写。
export function useUserSessionStore() {
  // null 表示尚未从 localStorage 恢复；恢复完成后才允许渲染主界面，
  // 否则 SSR 与 CSR 首次渲染不一致会触发 Hydration 报错。
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  // WebSocket 回调闭包会捕获旧的 currentUser，用 ref 始终指向最新值。
  const currentUserRef = useRef<UserProfile | null>(null);
  const [authReady, setAuthReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // access token 不再持久化，刷新页面必须先用 HttpOnly refresh cookie 换新令牌。
    // getAuthToken 会先迁移旧版本残留在 localStorage 的令牌，避免老会话失效。
    (async () => {
      try {
        let parsed: any = null;
        const saved = localStorage.getItem('omni_user');
        if (saved) parsed = JSON.parse(saved);
        if (parsed && parsed.isLoggedIn) {
          if (!getAuthToken()) {
            const renewed = await refreshAccessToken();
            if (!renewed) {
              localStorage.removeItem('omni_user');
              return;
            }
            parsed = JSON.parse(localStorage.getItem('omni_user') || 'null');
          }
          // 令牌绝不进 React state：资料编辑会把整个对象写回 localStorage。
          if (parsed && typeof parsed === 'object') delete parsed.token;
          // 身份以服务端为准：isAdmin 来自 ADMIN_USER_IDS 白名单，
          // 本地缓存可能是在被加入/移出白名单之前写下的。这里用一次轻量请求
          // 刷新，避免"已经是管理员却看不到管理入口"。
          try {
            const me = await apiFetch('/api/auth/me');
            if (me.ok) {
              const payload = await me.json().catch(() => null);
              if (payload?.user) {
                parsed = { ...parsed, ...payload.user, isLoggedIn: true };
                localStorage.setItem('omni_user', JSON.stringify(parsed));
              }
            }
          } catch {}
          if (!cancelled && parsed && parsed.isLoggedIn) setCurrentUser(parsed);
        }
      } catch {}
      if (!cancelled) setAuthReady(true);
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    currentUserRef.current = currentUser;
  }, [currentUser]);

  // 登录成功：标记登录态并持久化（同样不保存令牌）。
  const signIn = (user: UserProfile) => {
    const userState: UserProfile = { ...user, isLoggedIn: true };
    setCurrentUser(userState);
    localStorage.setItem('omni_user', JSON.stringify(userState));
  };

  // 资料更新：state 与 localStorage 必须一起改，否则刷新后回到旧资料。
  const updateProfile = (patch: Partial<UserProfile>) => {
    if (!currentUser) return;
    const updated = { ...currentUser, ...patch } as UserProfile;
    setCurrentUser(updated);
    localStorage.setItem('omni_user', JSON.stringify(updated));
  };

  // 退出登录：先撤销服务端会话，再清本地身份；网关不可用时也照常退出。
  const signOut = () => {
    void apiFetch('/api/auth/logout', { method: 'POST' }).catch(() => undefined).finally(() => {
      clearAccessToken();
      localStorage.removeItem('omni_user');
      setCurrentUser(null);
    });
  };

  return { currentUser, currentUserRef, authReady, setCurrentUser, signIn, updateProfile, signOut };
}
