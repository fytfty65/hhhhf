'use client';

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import {
  UserPlus, LogIn, Loader2, AlertCircle, ArrowRight, Eye, EyeOff, Sparkles
} from 'lucide-react';

export interface AuthUser {
  user_id: string;
  username: string;
  token: string;
}

const API_BASE = 'http://localhost:8080/api/v1';

function OmniMark() {
  return (
    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" className="w-14 h-14">
      <defs>
        <linearGradient id="authGrad1" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#d4924a" />
          <stop offset="100%" stopColor="#e8a860" />
        </linearGradient>
        <linearGradient id="authGrad2" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#d4924a" />
          <stop offset="100%" stopColor="#c8a870" />
        </linearGradient>
      </defs>
      <path d="M22 8C14.268 8 8 14.268 8 22C8 33.5 22 44 22 44C22 44 36 33.5 36 22C36 14.268 29.732 8 22 8Z" fill="url(#authGrad1)" fillOpacity="0.95" />
      <circle cx="22" cy="22" r="5" fill="#0a0a0f" />
      <path d="M30 14C24.477 14 20 18.477 20 24C20 32 30 40 30 40C30 40 40 32 40 24C40 18.477 35.523 14 30 14Z" fill="url(#authGrad2)" fillOpacity="0.95" />
      <circle cx="30" cy="24" r="3" fill="#0a0a0f" />
    </svg>
  );
}

export default function AuthPage({ onAuth }: { onAuth: (user: AuthUser) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const switchMode = () => {
    setMode(mode === 'login' ? 'register' : 'login');
    setError(null);
    setPassword('');
    setConfirmPassword('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!username.trim()) {
      setError('请输入用户名');
      return;
    }
    if (password.length < 6) {
      setError('密码至少需要 6 位');
      return;
    }
    if (mode === 'register' && password !== confirmPassword) {
      setError('两次输入的密码不一致');
      return;
    }

    setLoading(true);
    try {
      const endpoint = mode === 'login' ? '/auth/login' : '/auth/register';
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || '请求失败，请稍后再试');
      }

      const user: AuthUser = {
        user_id: data.user_id,
        username: data.username,
        token: data.token,
      };
      localStorage.setItem('omniroute_auth', JSON.stringify(user));
      onAuth(user);
    } catch (err: any) {
      setError(err.message || '网络异常，请确认网关服务已启动');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen font-sans bg-[#0a0a0f] text-stone-200 overflow-hidden relative flex items-center justify-center px-6">
      <div className="absolute inset-0 bg-gradient-to-br from-amber-500/[0.05] via-transparent to-amber-600/[0.04]" />
      <div className="absolute top-1/4 left-1/4 w-[340px] h-[340px] rounded-full bg-amber-500/[0.05] blur-[130px]" />
      <div className="absolute bottom-1/4 right-1/4 w-[260px] h-[260px] rounded-full bg-amber-400/[0.04] blur-[110px]" />

      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
        className="relative z-10 w-full max-w-md"
      >
        <div className="flex flex-col items-center mb-8">
          <OmniMark />
          <h1 className="text-2xl font-black text-stone-100 tracking-tight mt-5">
            Omni<span className="text-amber-400/90">Route</span>
          </h1>
          <p className="text-xs text-stone-500 font-medium mt-2 tracking-widest uppercase">
            智能体协同旅行中枢
          </p>
        </div>

        <div className="bg-[#0f0f15] border border-stone-800 rounded-3xl p-8 shadow-2xl">
          <div className="flex bg-stone-900/60 rounded-xl p-1 mb-7">
            <button
              onClick={() => switchMode()}
              className={`flex-1 py-2.5 rounded-lg text-sm font-bold transition-all ${mode === 'login' ? 'bg-amber-500 text-[#0a0a0f]' : 'text-stone-500 hover:text-stone-300'}`}
            >
              登录
            </button>
            <button
              onClick={() => switchMode()}
              className={`flex-1 py-2.5 rounded-lg text-sm font-bold transition-all ${mode === 'register' ? 'bg-amber-500 text-[#0a0a0f]' : 'text-stone-500 hover:text-stone-300'}`}
            >
              注册
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-wider mb-1.5 block">
                用户名
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入用户名"
                className="w-full px-4 py-3 bg-stone-900/40 border border-stone-800 rounded-xl text-sm text-stone-200 outline-none focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/10 transition-all placeholder:text-stone-600"
              />
            </div>

            <div>
              <label className="text-[10px] font-bold text-stone-500 uppercase tracking-wider mb-1.5 block">
                密码
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={mode === 'register' ? '至少 6 位' : '请输入密码'}
                  className="w-full px-4 py-3 pr-11 bg-stone-900/40 border border-stone-800 rounded-xl text-sm text-stone-200 outline-none focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/10 transition-all placeholder:text-stone-600"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-stone-500 hover:text-amber-400 transition-colors"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {mode === 'register' && (
              <div>
                <label className="text-[10px] font-bold text-stone-500 uppercase tracking-wider mb-1.5 block">
                  确认密码
                </label>
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="请再次输入密码"
                  className="w-full px-4 py-3 bg-stone-900/40 border border-stone-800 rounded-xl text-sm text-stone-200 outline-none focus:border-amber-500/60 focus:ring-2 focus:ring-amber-500/10 transition-all placeholder:text-stone-600"
                />
              </div>
            )}

            {error && (
              <motion.div
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                className="p-3 bg-rose-950/40 border border-rose-800/60 rounded-xl flex items-start gap-2"
              >
                <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                <span className="text-xs text-rose-300 font-medium">{error}</span>
              </motion.div>
            )}

            <button
              type="submit"
              disabled={loading}
              className={`w-full py-3.5 rounded-xl font-bold text-sm flex items-center justify-center gap-2 transition-all ${loading ? 'bg-stone-800 text-stone-400 cursor-not-allowed' : 'bg-amber-500 text-[#0a0a0f] hover:bg-amber-400 hover:-translate-y-0.5'}`}
            >
              {loading ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> 处理中...</>
              ) : (
                <>
                  {mode === 'login' ? <LogIn className="w-4 h-4" /> : <UserPlus className="w-4 h-4" />}
                  {mode === 'login' ? '登录进入中枢' : '创建账号'}
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>
        </div>

        <p className="text-center text-[10px] text-stone-600 font-bold mt-6 flex items-center justify-center gap-1.5">
          <Sparkles className="w-3 h-3 text-amber-500/60" />
          登录后进入多智能体旅行规划工作台
        </p>
      </motion.div>
    </div>
  );
}