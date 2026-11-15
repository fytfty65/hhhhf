'use client';

/**
 * 临时账号（免注册试用）的转正面板。
 *
 * 试用入口创建的是服务端真实账号，但密码是随机生成且从不展示，所以：
 *  - 数据其实在服务端，不是「只存在这台设备」；
 *  - 但登录态只存在于当前浏览器，清缓存/换设备后就登不回来；
 *  - 长期不用会被服务端清理。
 *
 * 这里把这三点如实说明，并提供原地转正：后端只改账号字段、不改 user_id，
 * 因此行程、记账、偏好都不需要迁移。
 */

import { useEffect, useState } from 'react';
import { AlertCircle, ArrowUpRight, Check, RefreshCw, ShieldCheck } from 'lucide-react';
import { apiJson } from '../lib/apiClient';
import type { UserProfile } from '../types';

type GuestPolicy = {
  retention_days?: number;
  cleanup_enabled?: boolean;
};

export default function GuestAccountPanel({
  user,
  className = '',
  onUpgraded,
}: {
  user: UserProfile;
  className?: string;
  onUpgraded: (patch: Partial<UserProfile>) => void;
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [nickname, setNickname] = useState(user.nickname || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [policy, setPolicy] = useState<GuestPolicy>({});

  useEffect(() => {
    let cancelled = false;
    apiJson<{ guest_policy?: GuestPolicy }>('/api/user/profile')
      .then((data) => {
        if (!cancelled) setPolicy(data.guest_policy || {});
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  const retentionDays = policy.retention_days ?? 30;
  const cleanupEnabled = policy.cleanup_enabled !== false;

  const upgrade = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    setMessage('');
    if (!username.trim()) {
      setError('请填写要使用的用户名');
      return;
    }
    if (password.length < 6) {
      setError('密码至少 6 位');
      return;
    }
    setBusy(true);
    try {
      const data = await apiJson<{ user?: { username?: string; nickname?: string } }>('/api/auth/upgrade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password, nickname: nickname.trim() }),
      });
      setPassword('');
      setMessage('已转为正式账号，行程与偏好都保留在原账号上。下次可用新用户名和密码登录。');
      onUpgraded({
        isGuest: false,
        username: data.user?.username || username.trim(),
        nickname: data.user?.nickname || nickname.trim() || user.nickname,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : '转正失败，请稍后再试');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={className} aria-labelledby="guest-heading" data-testid="guest-account-panel">
      <div className="flex items-center justify-between gap-2">
        <h2 id="guest-heading" className="inline-flex items-center gap-2 text-sm font-black text-slate-900">
          <ShieldCheck className="h-4 w-4 text-orange-500" /> 临时账号
        </h2>
        <span className="border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700">未转正</span>
      </div>

      <p className="mt-2 text-xs leading-5 text-slate-500">
        当前是免注册试用的临时账号，用户名是随机生成的，密码也没有展示给你。
        行程确实保存在服务端，但登录态只在这台设备的浏览器里：清理浏览器数据或换设备就找不回来。
        {cleanupEnabled
          ? `超过 ${retentionDays} 天未使用的临时账号会被自动清理。`
          : '过期清理当前已关闭，但登录态丢失后依然无法恢复。'}
      </p>

      {error && (
        <div role="alert" className="mt-3 flex items-start gap-2 border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] font-bold text-rose-700">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{error}
        </div>
      )}
      {message && (
        <div role="status" className="mt-3 flex items-start gap-2 border border-emerald-200 bg-emerald-50 px-3 py-2 text-[11px] font-bold text-emerald-700">
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />{message}
        </div>
      )}

      {!message && (
        <form onSubmit={upgrade} className="mt-4 space-y-2.5">
          <label className="block">
            <span className="mb-1 block text-[11px] font-bold text-slate-600">设置用户名</span>
            <input value={username} autoComplete="username" onChange={(event) => setUsername(event.target.value)}
              placeholder="例如 alice，不能以 guest_ 开头" maxLength={64}
              className="h-10 w-full border border-slate-200 bg-slate-50 px-3 text-xs outline-none focus:border-orange-400 focus:bg-white focus:ring-2 focus:ring-orange-100" />
          </label>
          <label className="block">
            <span className="mb-1 block text-[11px] font-bold text-slate-600">设置密码</span>
            <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)}
              placeholder="至少 6 位" className="h-10 w-full border border-slate-200 bg-slate-50 px-3 text-xs outline-none focus:border-orange-400 focus:bg-white focus:ring-2 focus:ring-orange-100" />
          </label>
          <label className="block">
            <span className="mb-1 block text-[11px] font-bold text-slate-600">昵称（可选）</span>
            <input value={nickname} onChange={(event) => setNickname(event.target.value)} maxLength={40}
              className="h-10 w-full border border-slate-200 bg-slate-50 px-3 text-xs outline-none focus:border-orange-400 focus:bg-white focus:ring-2 focus:ring-orange-100" />
          </label>
          <button type="submit" disabled={busy}
            className="inline-flex min-h-9 w-full items-center justify-center gap-2 bg-orange-500 px-3 text-xs font-bold text-white transition hover:bg-orange-600 disabled:opacity-60">
            {busy ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <ArrowUpRight className="h-3.5 w-3.5" />}
            保留全部数据并转正
          </button>
        </form>
      )}
    </section>
  );
}
