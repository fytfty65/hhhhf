'use client';

/**
 * AuthPortalScreen — 登录 / 注册全屏门户（带安全响应解析防崩处理）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 7)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { AlertCircle, ArrowRight, BrainCircuit, CheckCircle2, Lock, RefreshCw, User, Users } from 'lucide-react';
import { API_BASE } from '../lib/utils';
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');
    setSuccessMsg('');
    setLoading(true);

    const endpoint = tab === 'login' ? `${API_BASE}/api/auth/login` : `${API_BASE}/api/auth/register`;
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
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

      onLoginSuccess({ ...data.user, token: data.token });
    } catch (err: any) {
      setErrorMsg(err.message || '连接认证中枢失败，请检查网关服务与 API_BASE 配置');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center p-4 sm:p-8 font-sans bg-slate-50 relative overflow-hidden">
      <div className="absolute top-[-10%] left-[-10%] w-[45%] h-[45%] rounded-full bg-orange-400/15 blur-[140px] pointer-events-none" />
      <div className="absolute bottom-[-10%] right-[-10%] w-[45%] h-[45%] rounded-full bg-amber-400/15 blur-[140px] pointer-events-none" />

      <motion.div 
        initial={{ opacity: 0, scale: 0.96, y: 15 }} 
        animate={{ opacity: 1, scale: 1, y: 0 }} 
        transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        className="relative z-10 w-full max-w-[1050px] min-h-[620px] bg-white/80 backdrop-blur-3xl border border-white shadow-[0_12px_50px_rgba(0,0,0,0.06)] rounded-[2.5rem] grid grid-cols-1 lg:grid-cols-12 overflow-hidden"
      >
        {/* 左侧品牌与特色介绍 */}
        <div className="lg:col-span-6 p-10 sm:p-12 bg-gradient-to-br from-orange-500/10 via-amber-500/5 to-transparent border-r border-slate-100 flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-3.5 mb-8">
              <OmniLogo className="w-11 h-11" />
              <div>
                <h1 className="text-2xl font-black text-slate-900 tracking-tight">OmniRoute</h1>
                <p className="text-[10px] text-slate-500 font-extrabold tracking-widest uppercase mt-0.5">多智能体协同旅行中枢</p>
              </div>
            </div>

            <h2 className="text-2xl sm:text-3xl font-black text-slate-800 tracking-tight leading-snug mb-4">
              让每一次同行，<br/>都达成最优共识。
            </h2>
            <p className="text-xs sm:text-sm text-slate-500 font-medium leading-relaxed mb-8">
              依托底层多智能体博弈黑板与高维拓扑算法，为您与好友消除行程分歧，兼顾寻味、视觉大片与极致性价比。
            </p>

            <div className="space-y-3.5">
              <div className="flex items-center gap-3 p-3.5 bg-white/90 rounded-2xl border border-orange-100 shadow-2xs">
                <div className="w-8 h-8 rounded-xl bg-orange-50 text-orange-600 flex items-center justify-center font-bold text-xs">
                  <BrainCircuit className="w-4 h-4" />
                </div>
                <div>
                  <h4 className="text-xs font-bold text-slate-800">帕累托多智能体博弈</h4>
                  <p className="text-[11px] text-slate-400 font-medium">全员画像统一输入，自主寻优无冲突路线</p>
                </div>
              </div>

              <div className="flex items-center gap-3 p-3.5 bg-white/90 rounded-2xl border border-orange-100 shadow-2xs">
                <div className="w-8 h-8 rounded-xl bg-emerald-50 text-emerald-600 flex items-center justify-center font-bold text-xs">
                  <Users className="w-4 h-4" />
                </div>
                <div>
                  <h4 className="text-xs font-bold text-slate-800">跨端真实房间协同</h4>
                  <p className="text-[11px] text-slate-400 font-medium">输入专属房间码，多设备同频实时推演</p>
                </div>
              </div>
            </div>
          </div>

          <div className="pt-6 mt-6 border-t border-slate-200/60 flex items-center justify-between text-[11px] text-slate-400 font-bold">
            <span>SQLite 用户数据持久化安全存储</span>
            <span>v2.4 Pro</span>
          </div>
        </div>

        {/* 右侧登录 / 注册表单 */}
        <div className="lg:col-span-6 p-8 sm:p-12 flex flex-col justify-center bg-white/50">
          <div className="max-w-sm w-full mx-auto">
            <div className="text-left mb-6">
              <h3 className="text-2xl font-black text-slate-800">
                {tab === 'login' ? '旅行者登录' : '创建新账号'}
              </h3>
              <p className="text-xs text-slate-400 mt-1 font-medium">
                {tab === 'login' ? '请输入您的账号密码进入协同中枢' : '注册账号以保存您的个性化偏好与行程'}
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

            <form onSubmit={handleSubmit} className="space-y-4">
              {tab === 'register' && (
                <div>
                  <label className="text-xs font-bold text-slate-600 mb-1.5 block">旅行者昵称</label>
                  <div className="relative">
                    <User className="w-4 h-4 absolute left-3.5 top-3.5 text-slate-400" />
                    <input data-testid="auth-nickname"
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
                <label className="text-xs font-bold text-slate-600 mb-1.5 block">用户名 / 账号</label>
                <div className="relative">
                  <User className="w-4 h-4 absolute left-3.5 top-3.5 text-slate-400" />
                  <input data-testid="auth-username"
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
                <label className="text-xs font-bold text-slate-600 mb-1.5 block">密码</label>
                <div className="relative">
                  <Lock className="w-4 h-4 absolute left-3.5 top-3.5 text-slate-400" />
                  <input data-testid="auth-password"
                    type="password" 
                    required 
                    placeholder="输入密码" 
                    value={password} 
                    onChange={(e) => setPassword(e.target.value)} 
                    className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-xs outline-none focus:border-orange-500 focus:bg-white transition-all font-medium text-slate-800" 
                  />
                </div>
              </div>

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
          </div>
        </div>
      </motion.div>
    </div>
  );
}
