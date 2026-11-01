'use client';

/**
 * AuthPortalScreen — 登录 / 注册全屏门户（带安全响应解析防崩处理）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 7)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import React, { useId, useState } from 'react';
import { motion } from 'framer-motion';
import { AlertCircle, ArrowRight, BrainCircuit, CheckCircle2, Lock, RefreshCw, User, Users } from 'lucide-react';
import { apiFetch, setAccessToken } from '../lib/apiClient';
import OmniLogo from './OmniLogo';

// =========================================================================
// 门禁入口：主页面前置独立的 登录 / 注册 全屏门户组件 (带安全响应解析防崩处理)
// =========================================================================
export default function AuthPortalScreen({ onLoginSuccess }: { onLoginSuccess: (user: any) => void }) {
  const [tab, setTab] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [nickname, setNickname] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [loading, setLoading] = useState(false);
  // 找回密码：请求令牌 -> 用令牌改密，两步都在这个门户里完成。
  const [resetOpen, setResetOpen] = useState(false);
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [resetRequested, setResetRequested] = useState(false);
  const nicknameId = useId();
  const usernameId = useId();
  const passwordId = useId();
  const resetTokenId = useId();
  const newPasswordId = useId();

  const requestPasswordReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);
    try {
      const res = await apiFetch('/api/auth/password/forgot', {
        method: 'POST',
        auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || data?.message || `请求失败 (HTTP ${res.status})`);
      setResetRequested(true);
      // 生产环境只提示"若账号存在已发送"；开发环境后端会直接返回令牌。
      if (data?.reset_token) {
        setResetToken(data.reset_token);
        setSuccessMsg('开发环境已返回重置令牌，请设置新密码');
      } else {
        setSuccessMsg(data?.message || '如果账号存在，重置邮件已发送，请查收后填入令牌');
      }
    } catch (err: any) {
      setErrorMsg(err.message || '找回密码请求失败');
    } finally {
      setLoading(false);
    }
  };

  const submitPasswordReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);
    try {
      const res = await apiFetch('/api/auth/password/reset', {
        method: 'POST',
        auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken.trim(), new_password: newPassword.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || data?.message || `重置失败 (HTTP ${res.status})`);
      setResetOpen(false);
      setResetRequested(false);
      setResetToken('');
      setNewPassword('');
      setPassword('');
      setSuccessMsg('密码已重置，请使用新密码登录');
    } catch (err: any) {
      setErrorMsg(err.message || '密码重置失败');
    } finally {
      setLoading(false);
    }
  };

  // 免注册试用：用一次性随机账号注册并登录，直接进大厅。
  // 试用账号是真实账号（能在房间里协作、保存行程），只是名字是随机的；
  // 想跨设备同步时再注册正式账号即可。
  const startTrial = async () => {
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);
    const suffix = Math.random().toString(36).slice(2, 8) || String(Date.now()).slice(-6);
    const credentials = {
      username: `guest_${suffix}`,
      password: `tour-${suffix}-${Date.now()}`,
      nickname: `游客 ${suffix.slice(0, 4).toUpperCase()}`,
    };
    try {
      const reg = await apiFetch('/api/auth/register', {
        method: 'POST',
        auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      });
      const regText = await reg.text().catch(() => '');
      let regData: any = {};
      try { regData = JSON.parse(regText); } catch { regData = {}; }
      if (!reg.ok) {
        throw new Error(regData?.detail || regData?.message || regData?.error || `试用账号创建失败 (HTTP ${reg.status})`);
      }

      const login = await apiFetch('/api/auth/login', {
        method: 'POST',
        auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      });
      const loginText = await login.text().catch(() => '');
      let data: any = null;
      try { data = JSON.parse(loginText); } catch { data = null; }
      if (!login.ok || !data?.user) {
        throw new Error(data?.detail || data?.message || data?.error || `试用登录失败 (HTTP ${login.status})`);
      }
      setAccessToken(data.token);
      onLoginSuccess({ ...data.user });
    } catch (err: any) {
      setErrorMsg(err.message || '试用入口暂时不可用，请稍后再试或直接注册');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);

    const endpoint = tab === 'login' ? '/api/auth/login' : '/api/auth/register';
    try {
      const res = await apiFetch(endpoint, {
        method: 'POST',
        auth: false,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          username: username.trim(), 
          password: password.trim(), 
          nickname: nickname.trim() || username.trim() 
        })
      });

      // 👑 核心修复：先提取纯文本，防范后端非 200 返回纯文本或 HTML 报错导致 JSON.parse 崩溃
      const rawText = await res.text();
      let data: any = null;
      try {
        data = JSON.parse(rawText);
      } catch {
        throw new Error(`后端认证中枢响应异常 (HTTP ${res.status}): ${rawText.slice(0, 90) || '未返回有效 JSON 数据，请检查 Go 网关服务'}`);
      }

      if (!res.ok) {
        throw new Error(data?.detail || data?.message || data?.error || `请求失败 (HTTP ${res.status})，请检查账号密码`);
      }

      if (!data || !data.user) {
        throw new Error('认证响应格式异常，缺少 user 数据对象');
      }

      // 注册成功：不自动登录，切回登录页并提示；仅登录成功才进入主页面
      if (tab === 'register') {
        setSuccessMsg('注册成功，请使用新账号登录');
        setTab('login');
        setPassword('');
        setNickname('');
        return;
      }

      // Access token stays in memory; the refresh token arrived as an HttpOnly
      // cookie and is never visible to JavaScript.
      setAccessToken(data.token);
      onLoginSuccess({ ...data.user });
    } catch (err: any) {
      setErrorMsg(err.message || '连接认证中枢失败，请检查网关服务与 API_BASE 配置');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center px-4 pb-24 pt-4 sm:p-8 font-sans bg-slate-50 relative overflow-hidden">
      <div className="absolute top-[-10%] left-[-10%] w-[45%] h-[45%] rounded-full bg-orange-400/10 blur-[140px] pointer-events-none" />
      <div className="absolute bottom-[-10%] right-[-10%] w-[45%] h-[45%] rounded-full bg-amber-400/10 blur-[140px] pointer-events-none" />

      <motion.div 
        initial={{ opacity: 0, scale: 0.96, y: 15 }} 
        animate={{ opacity: 1, scale: 1, y: 0 }} 
        transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        className="relative z-10 w-full max-w-[1050px] lg:min-h-[620px] bg-white/80 backdrop-blur-3xl border border-white shadow-[0_12px_50px_rgba(0,0,0,0.06)] rounded-[2rem] sm:rounded-[2.5rem] grid grid-cols-1 lg:grid-cols-12 overflow-hidden"
      >
        {/* 左侧品牌与特色介绍：仅桌面端显示。
            小屏以前品牌介绍独占首屏，登录和试用都被压到折叠线下；
            移动端改为表单列顶部的紧凑品牌头，让登录 CTA 直接出现在首屏。 */}
        <div className="hidden lg:flex lg:col-span-6 p-10 sm:p-12 bg-gradient-to-br from-orange-500/8 via-amber-500/5 to-transparent border-r border-slate-100 flex-col justify-between">
          <div>
            <div className="flex items-center gap-3.5 mb-8">
              <OmniLogo className="w-11 h-11" />
              <div>
                <h1 className="text-2xl font-black text-slate-900 tracking-tight">OmniRoute</h1>
                <p className="text-xs text-slate-500 font-semibold tracking-wide mt-0.5">从想法到可执行行程</p>
              </div>
            </div>

            <h2 className="text-2xl sm:text-3xl font-black text-slate-800 tracking-tight leading-snug mb-4">
              让每一次同行，<br/>都达成最优共识。
            </h2>
            <p className="text-xs sm:text-sm text-slate-500 font-medium leading-relaxed mb-8">
              把目的地、时间、预算和同行人的要求放在一起，生成一份可以继续修改和执行的行程。
            </p>

            <div className="space-y-3.5">
              <div className="flex items-center gap-3 p-3.5 bg-white/90 rounded-2xl border border-orange-100 shadow-2xs">
                <div className="w-8 h-8 rounded-xl bg-orange-50 text-orange-600 flex items-center justify-center font-bold text-xs">
                  <BrainCircuit className="w-4 h-4" />
                </div>
                <div>
                  <h4 className="text-xs font-bold text-slate-800">多人偏好一起考虑</h4>
                  <p className="text-xs text-slate-400 font-medium">把节奏、预算和必去地点放进同一份方案</p>
                </div>
              </div>

              <div className="flex items-center gap-3 p-3.5 bg-white/90 rounded-2xl border border-orange-100 shadow-2xs">
                <div className="w-8 h-8 rounded-xl bg-emerald-50 text-emerald-600 flex items-center justify-center font-bold text-xs">
                  <Users className="w-4 h-4" />
                </div>
                <div>
                  <h4 className="text-xs font-bold text-slate-800">同行者实时协作</h4>
                  <p className="text-xs text-slate-400 font-medium">用房间码加入，在同一处查看和调整行程</p>
                </div>
              </div>
            </div>
          </div>

          <div className="pt-6 mt-6 border-t border-slate-200/60 flex items-center justify-between text-xs text-slate-400 font-bold">
            <span>行程保存在账号中，可跨设备同步</span>
            <span>随时可导出</span>
          </div>
        </div>

        {/* 右侧登录 / 注册表单 */}
        <div className="lg:col-span-6 p-6 sm:p-10 lg:p-12 flex flex-col justify-center bg-white/50">
          <div className="max-w-sm w-full mx-auto">
            {/* 移动端紧凑品牌头：不再让品牌介绍独占首屏，登录与试用直接可见 */}
            <div className="lg:hidden mb-6 flex items-center gap-3">
              <OmniLogo className="w-10 h-10" />
              <div>
                <p className="text-xl font-black text-slate-900 tracking-tight">OmniRoute</p>
                <p className="text-xs text-slate-500 font-semibold mt-0.5">从想法到可执行行程</p>
              </div>
            </div>
            <div className="text-left mb-6">
              <h3 className="text-2xl font-black text-slate-800">
                {tab === 'login' ? '旅行者登录' : '创建新账号'}
              </h3>
              <p className="text-xs text-slate-400 mt-1 font-medium">
                {tab === 'login' ? '登录后继续规划和管理你的行程' : '注册账号以保存您的个性化偏好与行程'}
              </p>
            </div>

            {/* Tab 切换 */}
            <div className="flex bg-slate-100 p-1 rounded-2xl mb-6">
              <button 
                data-testid="auth-login-tab"
                onClick={() => { setTab('login'); setErrorMsg(''); setSuccessMsg(''); }} 
                className={`flex-1 py-2.5 text-xs font-extrabold rounded-xl transition-all cursor-pointer ${tab === 'login' ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-500 hover:text-slate-700'}`}
              >
                账号登录
              </button>
              <button 
                data-testid="auth-register-tab"
                onClick={() => { setTab('register'); setErrorMsg(''); setSuccessMsg(''); }} 
                className={`flex-1 py-2.5 text-xs font-extrabold rounded-xl transition-all cursor-pointer ${tab === 'register' ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-500 hover:text-slate-700'}`}
              >
                快速注册
              </button>
            </div>

            {errorMsg && (
              <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="mb-4 p-3.5 bg-rose-50 border border-rose-200 rounded-2xl text-xs text-rose-600 font-bold flex items-start gap-2 shadow-2xs">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <span className="break-all">{errorMsg}</span>
              </motion.div>
            )}

            {successMsg && (
              <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="mb-4 p-3.5 bg-emerald-50 border border-emerald-200 rounded-2xl text-xs text-emerald-600 font-bold flex items-start gap-2 shadow-2xs">
                <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
                <span className="break-all">{successMsg}</span>
              </motion.div>
            )}

            {!resetOpen && (
            <form onSubmit={handleSubmit} className="space-y-4">
              {tab === 'register' && (
                <div>
                  <label htmlFor={nicknameId} className="text-xs font-bold text-slate-600 mb-1.5 block">旅行者昵称</label>
                  <div className="relative">
                    <User className="w-4 h-4 absolute left-3.5 top-3.5 text-slate-400" />
                    <input data-testid="auth-nickname" id={nicknameId}
                      type="text" 
                      required 
                      placeholder="例如：Felix / 丝路漫游者" 
                      value={nickname} 
                      onChange={(e) => setNickname(e.target.value)} 
                      className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-xs outline-none focus:border-orange-500 focus:bg-white transition-all font-medium text-slate-800" 
                    />
                  </div>
                </div>
              )}

              <div>
                <label htmlFor={usernameId} className="text-xs font-bold text-slate-600 mb-1.5 block">用户名 / 账号</label>
                <div className="relative">
                  <User className="w-4 h-4 absolute left-3.5 top-3.5 text-slate-400" />
                  <input data-testid="auth-username" id={usernameId}
                    type="text" 
                    required 
                    placeholder="输入您的账号名称" 
                    value={username} 
                    onChange={(e) => setUsername(e.target.value)} 
                    className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-xs outline-none focus:border-orange-500 focus:bg-white transition-all font-medium text-slate-800" 
                  />
                </div>
              </div>

              <div>
                <label htmlFor={passwordId} className="text-xs font-bold text-slate-600 mb-1.5 block">密码</label>
                <div className="relative">
                  <Lock className="w-4 h-4 absolute left-3.5 top-3.5 text-slate-400" />
                  <input data-testid="auth-password" id={passwordId}
                    type="password" 
                    required 
                    placeholder="输入密码" 
                    value={password} 
                    onChange={(e) => setPassword(e.target.value)} 
                    className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-xs outline-none focus:border-orange-500 focus:bg-white transition-all font-medium text-slate-800" 
                  />
                </div>
              </div>

              {tab === 'login' && (
                <button type="button" data-testid="auth-forgot" onClick={() => { setResetOpen(true); setErrorMsg(''); setSuccessMsg(''); }}
                  className="text-xs font-bold text-orange-600 hover:text-orange-700 underline underline-offset-4">
                  忘记密码？
                </button>
              )}

              <button data-testid="auth-submit"
                type="submit" 
                disabled={loading} 
                className="w-full mt-3 py-3.5 bg-orange-500 hover:bg-orange-600 active:scale-[0.99] text-white rounded-2xl text-xs font-extrabold transition-all shadow-lg shadow-orange-200 cursor-pointer flex items-center justify-center gap-2"
              >
                {loading ? (
                  <><RefreshCw className="w-4 h-4 animate-spin"/> 正在验证凭证...</>
                ) : (
                  <>{tab === 'login' ? '立即登录并进入大厅' : '注册并开启探索'} <ArrowRight className="w-4 h-4" /></>
                )}
              </button>
            </form>
            )}

            {/* 登录引导：第一次来可以先试用，不必先想账号密码 */}
            <div className="mt-5">
              <div className="flex items-center gap-3 text-xs font-bold text-slate-300">
                <span className="h-px flex-1 bg-slate-200" />
                或
                <span className="h-px flex-1 bg-slate-200" />
              </div>
              <button
                type="button"
                data-testid="auth-trial"
                onClick={startTrial}
                disabled={loading}
                className="mt-3 w-full py-3 rounded-2xl border border-slate-200 bg-white text-xs font-extrabold text-slate-700 transition-all hover:border-orange-200 hover:bg-orange-50/50 disabled:opacity-60 cursor-pointer"
              >
                先随便逛逛 · 免注册试用
              </button>
              <p className="mt-2 text-xs leading-5 font-medium text-slate-400">
                试用会创建一个随机昵称的账号，直接进入大厅；行程保存在这台设备，注册正式账号后可跨设备同步。
              </p>
              {tab === 'login' && (
                <p className="mt-2 text-xs font-medium text-slate-400">
                  还没有账号？
                  <button type="button" onClick={() => { setTab('register'); setErrorMsg(''); setSuccessMsg(''); }}
                    className="ml-1 font-bold text-orange-600 underline underline-offset-2 hover:text-orange-700 cursor-pointer">
                    30 秒快速注册
                  </button>
                </p>
              )}
            </div>

            {resetOpen && (
              <div className="space-y-4">
                <div>
                  <p className="text-sm font-black text-slate-800">找回密码</p>
                  <p className="mt-1 text-xs font-medium leading-5 text-slate-500">
                    先提交账号获取重置令牌；生产环境令牌会发送到绑定邮箱，开发环境直接显示。
                  </p>
                </div>
                {!resetRequested ? (
                  <form onSubmit={requestPasswordReset} className="space-y-4">
                    <div>
                      <label htmlFor={usernameId} className="text-xs font-bold text-slate-600 mb-1.5 block">用户名 / 账号</label>
                      <div className="relative">
                        <User className="w-4 h-4 absolute left-3.5 top-3.5 text-slate-400" />
                        <input id={usernameId} data-testid="reset-username" type="text" required
                          placeholder="输入要找回的账号" value={username} onChange={(e) => setUsername(e.target.value)}
                          className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-xs outline-none focus:border-orange-500 focus:bg-white transition-all font-medium text-slate-800" />
                      </div>
                    </div>
                    <button type="submit" data-testid="reset-request" disabled={loading}
                      className="w-full py-3 bg-orange-500 hover:bg-orange-600 text-white rounded-2xl text-xs font-extrabold transition-all disabled:opacity-60">
                      {loading ? '正在提交…' : '获取重置令牌'}
                    </button>
                  </form>
                ) : (
                  <form onSubmit={submitPasswordReset} className="space-y-4">
                    <div>
                      <label htmlFor={resetTokenId} className="text-xs font-bold text-slate-600 mb-1.5 block">重置令牌</label>
                      <input id={resetTokenId} data-testid="reset-token" type="text" required
                        placeholder="粘贴邮件中的重置令牌" value={resetToken} onChange={(e) => setResetToken(e.target.value)}
                        className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-xs outline-none focus:border-orange-500 focus:bg-white transition-all font-medium text-slate-800" />
                    </div>
                    <div>
                      <label htmlFor={newPasswordId} className="text-xs font-bold text-slate-600 mb-1.5 block">新密码</label>
                      <div className="relative">
                        <Lock className="w-4 h-4 absolute left-3.5 top-3.5 text-slate-400" />
                        <input id={newPasswordId} data-testid="reset-new-password" type="password" required
                          placeholder="输入新密码" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                          className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-xs outline-none focus:border-orange-500 focus:bg-white transition-all font-medium text-slate-800" />
                      </div>
                    </div>
                    <button type="submit" data-testid="reset-submit" disabled={loading || !resetToken.trim() || !newPassword.trim()}
                      className="w-full py-3 bg-orange-500 hover:bg-orange-600 text-white rounded-2xl text-xs font-extrabold transition-all disabled:opacity-60">
                      {loading ? '正在重置…' : '设置新密码'}
                    </button>
                  </form>
                )}
                <button type="button" onClick={() => { setResetOpen(false); setResetRequested(false); setErrorMsg(''); setSuccessMsg(''); }}
                  className="w-full text-xs font-bold text-slate-500 hover:text-slate-800">
                  返回登录
                </button>
              </div>
            )}
          </div>
        </div>
      </motion.div>
    </div>
  );
}
