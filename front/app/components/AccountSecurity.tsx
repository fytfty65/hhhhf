'use client';

/**
 * 账号安全：改密、设备会话与「退出所有设备」。
 *
 * 后端已经把凭证拆成短期 access token + HttpOnly refresh cookie，并记录了
 * 每个设备的会话；这里把这些能力真正呈现给用户，否则「能撤销」只存在于 API 里。
 * 所有金额无关，故不涉及 precision；时间直接显示本地时区。
 */

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Check, Eye, EyeOff, LogOut, Monitor, RefreshCw, ShieldCheck } from 'lucide-react';
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
  const [showCurrentPassword, setShowCurrentPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);

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
    <section className={className} aria-labelledby="security-heading" data-testid="account-security">
      <div className="pj-side-head">
        <h2 id="security-heading"><ShieldCheck aria-hidden="true" />账号安全</h2>
        <button
          type="button"
          onClick={() => void loadSessions()}
          disabled={loading || busy}
          className="pj-icon-button"
          title="刷新会话列表"
          aria-label="刷新会话列表"
        >
          <RefreshCw className={loading ? 'pj-spin' : undefined} aria-hidden="true" />
        </button>
      </div>

      {(error || message) && (
        <div className="pj-sec-errors">
          {error && (
            <div role="alert" className="pj-sec-error"><AlertCircle aria-hidden="true" />{error}</div>
          )}
          {message && (
            <div role="status" className="pj-sec-message"><Check aria-hidden="true" />{message}</div>
          )}
        </div>
      )}

      <form onSubmit={changePassword} className="pj-sec-form">
        <label className="pj-sec-field">
          <span>当前密码</span>
          <div className="pj-password-wrap">
            <input
              type={showCurrentPassword ? 'text' : 'password'}
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
            <button
              type="button"
              className="pj-password-toggle"
              onClick={() => setShowCurrentPassword((value) => !value)}
              aria-label={showCurrentPassword ? '隐藏当前密码' : '显示当前密码'}
              title={showCurrentPassword ? '隐藏当前密码' : '显示当前密码'}
            >
              {showCurrentPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
            </button>
          </div>
        </label>
        <label className="pj-sec-field">
          <span>新密码</span>
          <div className="pj-password-wrap">
            <input
              type={showNewPassword ? 'text' : 'password'}
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
            <button
              type="button"
              className="pj-password-toggle"
              onClick={() => setShowNewPassword((value) => !value)}
              aria-label={showNewPassword ? '隐藏新密码' : '显示新密码'}
              title={showNewPassword ? '隐藏新密码' : '显示新密码'}
            >
              {showNewPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
            </button>
          </div>
        </label>
        <button type="submit" disabled={busy} className="pj-primary-full">更新密码</button>
      </form>

      <div className="pj-sec-devices">
        <div className="pj-sec-devices-head">
          <p>已登录设备</p>
          <span>{sessions.length} 台</span>
        </div>
        {loading ? (
          <p className="pj-device-empty inline-flex items-center gap-1.5"><RefreshCw className="pj-spin" aria-hidden="true" />正在同步会话…</p>
        ) : sessions.length === 0 ? (
          <p className="pj-device-empty">当前没有可用的设备会话。</p>
        ) : (
          <div className="pj-device-list">
            {sessions.map((session) => (
              <div key={session.id} className="pj-device-card">
                <span className="pj-device-icon" aria-hidden="true"><Monitor /></span>
                <div className="pj-device-copy">
                  <p className="pj-device-name">
                    <span>{formatDevice(session)}</span>
                    {session.current && <span className="pj-device-now">本机</span>}
                  </p>
                  <p className="pj-device-meta">{session.ip || '未知 IP'} · 最近使用 {formatTime(session.last_used_at)}</p>
                </div>
                <button type="button" onClick={() => void revoke(session)} disabled={busy} className="pj-device-exit">退出</button>
              </div>
            ))}
          </div>
        )}
        <button type="button" onClick={() => void logoutAll()} disabled={busy} className="pj-logout-all">
          <LogOut aria-hidden="true" /> 退出所有设备
        </button>
      </div>
    </section>
  );
}
