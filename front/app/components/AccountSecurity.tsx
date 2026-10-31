'use client';

/**
 * 账号安全：改密、设备会话与"退出所有设备"。
 *
 * 后端已经把凭证拆成短期 access token + HttpOnly refresh cookie，并记录了
 * 每个设备的会话；这里把这些能力真正呈现给用户，否则"能撤销"只存在于 API 里。
 * 所有金额无关，故不涉及 precision；时间直接显示本地时区。
 */

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Check, LogOut, RefreshCw, ShieldCheck, Smartphone } from 'lucide-react';
import { apiJson } from '../lib/apiClient';

type DeviceSession = {
  id: string;
  user_agent?: string;
  ip?: string;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  current: boolean;
};

function formatDevice(session: DeviceSession): string {
  const agent = (session.user_agent || '').toLowerCase();
  if (!agent) return '未知设备';
  if (agent.includes('iphone')) return 'iPhone';
  if (agent.includes('ipad')) return 'iPad';
  if (agent.includes('android')) return 'Android 设备';
  if (agent.includes('windows')) return 'Windows 设备';
  if (agent.includes('mac os') || agent.includes('macintosh')) return 'Mac';
  if (agent.includes('linux')) return 'Linux 设备';
  return session.user_agent || '未知设备';
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false });
}

export default function AccountSecurity({ className = '' }: { className?: string }) {
  const [sessions, setSessions] = useState<DeviceSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');

  const loadSessions = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await apiJson<{ sessions?: DeviceSession[] }>('/api/auth/sessions');
      setSessions(Array.isArray(data.sessions) ? data.sessions : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : '会话列表读取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const changePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    setMessage('');
    if (!currentPassword || !newPassword) {
      setError('请填写当前密码和新密码');
      return;
    }
    setBusy(true);
    try {
      await apiJson('/api/auth/password/change', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      setCurrentPassword('');
      setNewPassword('');
      setMessage('密码已更新，其他设备已退出');
      // 其他设备被撤销，列表需要重新拉取。
      await loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : '修改密码失败');
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (session: DeviceSession) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await apiJson(`/api/auth/sessions/${encodeURIComponent(session.id)}`, { method: 'DELETE' });
      if (session.current) {
        // 当前设备已撤销：刷新回到登录门户，避免界面继续持有失效凭证。
        window.location.reload();
        return;
      }
      setMessage('已退出该设备');
      await loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : '退出设备失败');
    } finally {
      setBusy(false);
    }
  };

  const logoutAll = async () => {
    if (typeof window !== 'undefined' && !window.confirm('确定退出所有设备吗？当前设备也需要重新登录。')) return;
    setBusy(true);
    setError('');
    try {
      await apiJson('/api/auth/logout-all', { method: 'POST' });
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : '退出所有设备失败');
      setBusy(false);
    }
  };

  return (
    <section className={`${className}`} aria-labelledby="security-heading">
      <div className="flex items-center justify-between gap-2">
        <h2 id="security-heading" className="inline-flex items-center gap-2 text-sm font-black text-slate-900">
          <ShieldCheck className="h-4 w-4 text-orange-500" /> 账号安全
        </h2>
        <button type="button" onClick={() => void loadSessions()} disabled={loading || busy}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50"
          title="刷新会话列表" aria-label="刷新会话列表">
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

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

      <form onSubmit={changePassword} className="mt-4 space-y-2.5">
        <label className="block">
          <span className="mb-1 block text-[11px] font-bold text-slate-600">当前密码</span>
          <input type="password" autoComplete="current-password" value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            className="h-10 w-full border border-slate-200 bg-slate-50 px-3 text-xs outline-none focus:border-orange-400 focus:bg-white focus:ring-2 focus:ring-orange-100" />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] font-bold text-slate-600">新密码</span>
          <input type="password" autoComplete="new-password" value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            className="h-10 w-full border border-slate-200 bg-slate-50 px-3 text-xs outline-none focus:border-orange-400 focus:bg-white focus:ring-2 focus:ring-orange-100" />
        </label>
        <button type="submit" disabled={busy}
          className="inline-flex min-h-9 w-full items-center justify-center gap-2 bg-orange-500 px-3 text-xs font-bold text-white transition hover:bg-orange-600 disabled:opacity-60">
          更新密码
        </button>
      </form>

      <div className="mt-4 border-t border-slate-100 pt-3">
        <p className="text-[11px] font-black uppercase tracking-[0.14em] text-slate-400">已登录设备</p>
        {loading ? (
          <p className="mt-2 inline-flex items-center gap-2 text-xs font-semibold text-slate-400"><RefreshCw className="h-3.5 w-3.5 animate-spin" />正在同步会话…</p>
        ) : sessions.length === 0 ? (
          <p className="mt-2 text-xs leading-5 text-slate-500">当前没有可用的设备会话。</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {sessions.map((session) => (
              <li key={session.id} className="flex items-start justify-between gap-2 border border-slate-100 bg-slate-50/60 px-2.5 py-2">
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 text-xs font-bold text-slate-700">
                    <Smartphone className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                    <span className="truncate">{formatDevice(session)}</span>
                    {session.current && <span className="shrink-0 border border-emerald-200 bg-emerald-50 px-1.5 text-[10px] font-bold text-emerald-700">本机</span>}
                  </p>
                  <p className="mt-0.5 truncate text-[10px] font-semibold text-slate-400">
                    {session.ip || '未知 IP'} · 最近使用 {formatTime(session.last_used_at)}
                  </p>
                </div>
                <button type="button" onClick={() => void revoke(session)} disabled={busy}
                  className="shrink-0 rounded px-2 py-1 text-[10px] font-bold text-rose-600 transition hover:bg-rose-50 disabled:opacity-50">
                  退出
                </button>
              </li>
            ))}
          </ul>
        )}
        <button type="button" onClick={() => void logoutAll()} disabled={busy}
          className="mt-3 inline-flex min-h-9 w-full items-center justify-center gap-2 border border-rose-200 bg-rose-50 px-3 text-xs font-bold text-rose-700 transition hover:bg-rose-100 disabled:opacity-60">
          <LogOut className="h-3.5 w-3.5" /> 退出所有设备
        </button>
      </div>
    </section>
  );
}
