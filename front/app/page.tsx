'use client';

import dynamic from 'next/dynamic';
import React, { useState, useRef, useEffect, useMemo } from 'react';
import { 
  Users, MapPin, Sparkles, Coffee, Camera, Car, PiggyBank, ArrowRight, Check, 
  UserPlus, Map as MapIcon, Compass, Headphones, TrendingDown, RefreshCw, 
  X, AlertCircle, Clock, ThumbsUp, ThumbsDown, BrainCircuit,
  MessageSquare, Wand2, ArrowDown, Sun, ExternalLink, Hotel, RotateCw, Split, Scale,
  Lock, User, LogIn, LogOut, Edit3, KeyRound, CheckCircle2, ChevronDown, ChevronUp, Copy,
  Compass as CompassIcon, ShieldCheck, ArrowLeft, CalendarDays, Route, Save, Shuffle,
  CalendarClock, Heart, Send, Plus, MessageCircle, Flame, Mic, Navigation,
  Trophy, Award, Medal, Star, Mountain, UtensilsCrossed, Landmark, Quote, BookOpen, Footprints
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// 引入 3D 到 2D 的全域路由调度器
import FullRouteVisualizer from './FullRouteVisualizer';

// 自定义头像上传与裁剪组件
import AvatarUploader from './components/AvatarUploader';

// 目的地图谱数据（用于主页灵感目的地推荐）
import { PROVINCE_DATA } from './data/provinceData';

// 精简行政区名称（如 "西藏自治区" -> "西藏"）
function shortenRegionName(full: string) {
  return full.replace(/(壮族自治区|回族自治区|维吾尔自治区|特别行政区|自治区|省|市)$/, '');
}

// 全站评分最高的目的地（用于主页灵感目的地图谱卡片）
const TOP_DESTINATIONS = Object.entries(PROVINCE_DATA)
  .sort((a, b) => parseFloat(b[1].summary.score) - parseFloat(a[1].summary.score))
  .slice(0, 6)
  .map(([name]) => name);

const API_BASE = "http://localhost:8080";
const WS_BASE = "ws://localhost:8080";

const InteractiveAmapComponent = dynamic(
  () => import('./InteractiveAmapComponent'),
  {
    ssr: false,
    loading: () => (
      <div className="absolute inset-0 w-full h-full bg-slate-50 flex flex-col items-center justify-center gap-3">
        <RefreshCw className="w-6 h-6 text-orange-500 animate-spin"/>
        <span className="text-sm font-bold text-slate-400 tracking-wider">空间拓扑引擎装载中...</span>
      </div>
    )
  }
);

// 灵感目的地图谱（高德瓦片 + MapLibre 交互式目的地可视化）
const DestinationMap = dynamic(() => import('./DestinationMap'), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 z-[130] bg-slate-950 flex items-center justify-center">
      <RefreshCw className="w-6 h-6 text-orange-500 animate-spin" />
    </div>
  )
});

// ================= 系统图标 =================
function OmniLogo({ className = "w-8 h-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
      <defs>
        <linearGradient id="warmGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#FF8E53" /> 
          <stop offset="100%" stopColor="#FF6B6B" />
        </linearGradient>
        <linearGradient id="coolGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#4FACFE" /> 
          <stop offset="100%" stopColor="#00F2FE" />
        </linearGradient>
      </defs>
      <path d="M22 8C14.268 8 8 14.268 8 22C8 33.5 22 44 22 44C22 44 36 33.5 36 22C36 14.268 29.732 8 22 8Z" fill="url(#warmGrad)" fillOpacity="0.9" style={{ mixBlendMode: 'normal' }}/>
      <circle cx="22" cy="22" r="5" fill="#FFFFFF" />
      <path d="M30 14C24.477 14 20 18.477 20 24C20 32 30 40 30 40C30 40 40 32 40 24C40 18.477 35.523 14 30 14Z" fill="url(#coolGrad)" fillOpacity="0.95" style={{ mixBlendMode: 'normal' }}/>
      <circle cx="30" cy="24" r="3" fill="#FFFFFF" />
    </svg>
  );
}

// ================= JSON 与 数据提解助手 =================
function tryExtractJson(text: string) {
  if (!text) return null;
  let jsonCandidate = "";

  if (text.includes("[FINAL_JSON]")) {
    jsonCandidate = text.split("[FINAL_JSON]")[1];
  } else if (text.includes("```json")) {
    jsonCandidate = text.split("```json")[1];
  } else {
    const firstBrace = text.search(/\{\s*"status"|\{\s*"route"/);
    if (firstBrace !== -1) {
      jsonCandidate = text.substring(firstBrace);
    }
  }

  if (!jsonCandidate) return null;
  jsonCandidate = jsonCandidate.replace(/```json/g, "").replace(/```/g, "").trim();

  const lastBraceIdx = jsonCandidate.lastIndexOf("}");
  if (lastBraceIdx === -1) return null;

  const validSubstring = jsonCandidate.substring(0, lastBraceIdx + 1);
  try {
    return JSON.parse(validSubstring);
  } catch (e) {
    return null;
  }
}

// 👑 坐标归一化：兼容 [lng,lat]、"lng,lat"、{lng,lat}/{lon,lat} 等多种后端/LLM 输出，杜绝 mapbox LngLatLike 崩溃
function normalizeLnglat(v: any): [number, number] | null {
  if (Array.isArray(v) && v.length >= 2) {
    const lng = Number(v[0]);
    const lat = Number(v[1]);
    if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
    return null;
  }
  if (typeof v === 'string') {
    const parts = v.split(',');
    if (parts.length >= 2) {
      const lng = Number(parts[0]);
      const lat = Number(parts[1]);
      if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
    }
    return null;
  }
  if (v && typeof v === 'object') {
    const lng = Number((v as any).lng ?? (v as any).lon ?? (v as any).longitude);
    const lat = Number((v as any).lat ?? (v as any).latitude);
    if (Number.isFinite(lng) && Number.isFinite(lat)) return [lng, lat];
  }
  return null;
}

function extractStreamingRoutes(text: string): any[] {
  if (!text) return [];
  let jsonPart = text;
  if (text.includes("[FINAL_JSON]")) {
    jsonPart = text.split("[FINAL_JSON]")[1];
  } else if (text.includes("```json")) {
    jsonPart = text.split("```json")[1];
  }

  const objectMatches = jsonPart.match(/\{\s*"day"\s*:\s*\d+[\s\S]*?\}/g);
  if (!objectMatches) return [];

  const parsedItems: any[] = [];
  for (const matchStr of objectMatches) {
    try {
      const item = JSON.parse(matchStr);
      if (item && (item.location || item.name)) {
        parsedItems.push({
          day: item.day || 1,
          name: item.location || item.name,
          lnglat: normalizeLnglat(item.lnglat) || [87.61, 43.82],
          color: item.tags?.includes("寻味") || item.type === "food" ? "#f97316" : "#3b82f6",
          desc: item.desc || item.action || "",
          time: item.time || "",
          time_reason: item.time_reason || "",
          transport: item.transport || "",
          tags: item.tags || [],
          cost: item.cost_estimate || item.cost || "¥45/人",
          photos: item.photos || [],
          trust_reason: item.trust_reason || "核心地标推荐",
          amap_url: item.amap_url || "",
          hotel_candidates: item.hotel_candidates || [],
          split_info: item.split_info || "",
          merge_point: Boolean(item.merge_point),
          is_hotel: Boolean(item.is_hotel || item.tags?.includes("住宿"))
        });
      }
    } catch (e) {
      // 容错流式单项解析
    }
  }
  return parsedItems;
}

// 把协议相对 `//` 或 http 图片统一升级为 https，并对高德图片 CDN 显式禁用 Referer，
// 绕过 store.is.autonavi.com 的防盗链/Referer 校验 —— 这是浏览器端图片“完全不可见”的根因。
function getCleanPhotoUrl(photoUrl?: string, poiName: string = '', photoIndex: number = 0) {
  if (!photoUrl || typeof photoUrl !== 'string') return '';
  let u = photoUrl.trim();
  if (u.startsWith('//')) u = 'https:' + u;
  else if (u.startsWith('http://')) u = 'https://' + u.slice('http://'.length);
  return u.startsWith('https://') ? u : '';
}

// 👑 图片组件：真实高德实景照片 -> 高德坐标静态地图（与实际地理位置严格一致）-> 明确标注「实景暂不可用」，绝不使用与地点无关的虚假图片
function PoiImage({ src, fallback, name, className = '' }: { src?: string; fallback?: string; name: string; className?: string }) {
  // 初始阶段智能判定：主图缺失但兜底实时存在时直接进入兜底，避免“实景照片为空却静态地图可用”时仍显示空白占位
  const [stage, setStage] = useState<'src' | 'fallback' | 'none'>(() => (src ? 'src' : fallback ? 'fallback' : 'none'));

  let current = '';
  if (stage === 'src' && src) current = src;
  else if (stage === 'fallback' && fallback) current = fallback;

  if (!current || stage === 'none') {
    // 无真实照片且无静态地图时，展示明确的地名占位，绝不填充虚假图片
    return (
      <div className={`flex flex-col items-center justify-center gap-1 bg-slate-100 dark:bg-slate-800 ${className}`}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="w-8 h-8 text-slate-300 dark:text-slate-600">
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <circle cx="8.5" cy="10" r="1.5" />
          <path d="m21 15-5-5L5 21" />
        </svg>
        <span className="text-[11px] font-bold text-slate-400 dark:text-slate-500 leading-tight text-center px-2">{name}·实景暂不可用</span>
      </div>
    );
  }

  return (
    <img
      src={current}
      alt={name}
      onError={() => setStage(prev => (prev === 'src' ? 'fallback' : 'none'))}
      className={className}
      loading="lazy"
      referrerPolicy="no-referrer"
    />
  );
}

type Phase = 'drafting' | 'deduction' | 'decision';

export interface UserProfile {
  id: string;
  username: string;
  nickname: string;
  avatarSeed: string;
  avatarUrl?: string;
  signature?: string;
  isLoggedIn: boolean;
}

export interface RoomMember {
  id: string;
  name: string;
  role: string;
  intent: string;
  avatarSeed: string;
}



// =========================================================================
// 门禁入口：主页面前置独立的 登录 / 注册 全屏门户组件 (带安全响应解析防崩处理)
// =========================================================================
function AuthPortalScreen({ onLoginSuccess }: { onLoginSuccess: (user: any) => void }) {
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

      onLoginSuccess(data.user);
    } catch (err: any) {
      setErrorMsg(err.message || '连接认证中枢失败，请检查 Python 后端服务是否正常启动在 8080 端口');
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
                onClick={() => { setTab('login'); setErrorMsg(''); setSuccessMsg(''); }} 
                className={`flex-1 py-2.5 text-xs font-extrabold rounded-xl transition-all cursor-pointer ${tab === 'login' ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-500 hover:text-slate-700'}`}
              >
                账号登录
              </button>
              <button 
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
                    <input 
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
                  <input 
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
                  <input 
                    type="password" 
                    required 
                    placeholder="输入密码" 
                    value={password} 
                    onChange={(e) => setPassword(e.target.value)} 
                    className="w-full pl-10 pr-4 py-3 bg-slate-50 border border-slate-200 rounded-2xl text-xs outline-none focus:border-orange-500 focus:bg-white transition-all font-medium text-slate-800" 
                  />
                </div>
              </div>

              <button 
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

// =========================================================================
// 个人主页：展示用户资料、个性头像与历史行程安排表
// =========================================================================
interface ProfileTrip {
  ID: string;
  UserID: string;
  Title: string;
  DestCity: string;
  Content: string;
  CreatedAt: string;
}

function ProfileScreen({ currentUser, onBack, onProfileChange }: { currentUser: UserProfile; onBack: () => void; onProfileChange: (patch: Partial<UserProfile>) => void }) {
  const [trips, setTrips] = useState<ProfileTrip[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState('');
  const [avatarSeed, setAvatarSeed] = useState(currentUser.avatarSeed || currentUser.username);
  const [avatarUrl, setAvatarUrl] = useState(currentUser.avatarUrl || '');
  const [signature, setSignature] = useState(currentUser.signature || '');
  const [avatarSaving, setAvatarSaving] = useState(false);
  const [avatarSaved, setAvatarSaved] = useState(false);
  const [expandedTrip, setExpandedTrip] = useState<string | null>(null);
  const [publishedTripId, setPublishedTripId] = useState<string | null>(null);
  const [myPosts, setMyPosts] = useState<CommunityPostItem[]>([]);
  const [socialLoading, setSocialLoading] = useState(true);
  const [showUploader, setShowUploader] = useState(false);
  const [showEditor, setShowEditor] = useState(false);
  const [editNickname, setEditNickname] = useState(currentUser.nickname || currentUser.username);
  const [editSignature, setEditSignature] = useState(currentUser.signature || '');
  const [profileSaving, setProfileSaving] = useState(false);

  const loadProfile = async () => {
    setLoading(true);
    setErrorMsg('');
    try {
      const res = await fetch(`${API_BASE}/api/user/profile?user_id=${encodeURIComponent(currentUser.id)}`);
      const rawText = await res.text();
      let data: any = null;
      try { data = JSON.parse(rawText); } catch { throw new Error(`个人主页响应异常 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '个人主页加载失败');
      setTrips(data.trips || []);
      if (data.user) {
        if (data.user.avatarUrl) setAvatarUrl(data.user.avatarUrl);
        if (data.user.signature) setSignature(data.user.signature);
      }
    } catch (e: any) {
      setErrorMsg(e.message || '个人主页加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadProfile(); loadSocial(); }, [currentUser.id]);

  const loadSocial = async () => {
    setSocialLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/community/list`);
      const raw = await res.text();
      let data: any = null; try { data = JSON.parse(raw); } catch { return; }
      const mine = (data?.posts || []).filter((p: any) => p.user_id === currentUser.id);
      setMyPosts(mine);
    } catch {} finally { setSocialLoading(false); }
  };

  const handleRandomAvatar = () => {
    setAvatarSeed(Math.random().toString(36).substring(2, 10));
  };

  const handleSaveAvatar = async () => {
    setAvatarSaving(true);
    try {
      const res = await fetch(`${API_BASE}/api/user/avatar`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, avatar_seed: avatarSeed })
      });
      const rawText = await res.text();
      let data: any = null;
      try { data = JSON.parse(rawText); } catch { throw new Error(`头像响应异常 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '头像更新失败');
      setAvatarSaved(true);
      setTimeout(() => setAvatarSaved(false), 1500);
      onProfileChange({ avatarSeed });
    } catch (e: any) {
      setErrorMsg(e.message || '头像更新失败');
    } finally {
      setAvatarSaving(false);
    }
  };

  const handleAvatarUploaded = (url: string) => {
    setAvatarUrl(url);
    onProfileChange({ avatarUrl: url });
  };

  const handleSaveProfile = async () => {
    const nickname = editNickname.trim();
    if (!nickname) {
      setErrorMsg('昵称不能为空');
      return;
    }
    setProfileSaving(true);
    try {
      const res = await fetch(`${API_BASE}/api/user/profile/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, nickname, signature: editSignature.trim() })
      });
      const rawText = await res.text();
      let data: any = null;
      try { data = JSON.parse(rawText); } catch { throw new Error(`资料响应异常 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '资料更新失败');
      setSignature(data.signature || '');
      onProfileChange({ nickname: data.nickname || nickname, signature: data.signature || '' });
      setShowEditor(false);
      setErrorMsg('');
    } catch (e: any) {
      setErrorMsg(e.message || '资料更新失败');
    } finally {
      setProfileSaving(false);
    }
  };

  const formatDate = (s: string) => {
    if (!s) return '时间未知';
    const d = new Date(s);
    return isNaN(d.getTime()) ? s : d.toLocaleDateString('zh-CN');
  };

  const parseRoutes = (content: string) => {
    try {
      const obj = JSON.parse(content);
      if (Array.isArray(obj)) return obj;
      if (obj && Array.isArray(obj.routes)) return obj.routes;
      return [];
    } catch { return []; }
  };

  const summarizeTrip = (content: string) => {
    try {
      const obj = JSON.parse(content);
      if (obj && typeof obj.summary === 'string' && obj.summary) return obj.summary;
      if (obj && Array.isArray(obj.routes)) return `已规划 ${obj.routes.length} 个行程节点`;
      return content || '';
    } catch { return content || ''; }
  };

  const tripStats = useMemo(() => {
    const cities = new Set(trips.map(t => t.DestCity).filter(Boolean));
    return {
      total: trips.length,
      cityCount: cities.size,
      cities: Array.from(cities)
    };
  }, [trips]);

  // 由真实行程节点聚合出的兴趣标签（类型 + 标签）
  const interestTags = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of trips) {
      const routes = parseRoutes(t.Content);
      for (const r of routes) {
        const type = String(r.type || '');
        if (type) counts[type] = (counts[type] || 0) + 1;
        for (const tag of (r.tags || [])) {
          if (tag && tag !== '智能补全') counts[tag] = (counts[tag] || 0) + 1;
        }
      }
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [trips]);

  // 由真实数据解锁的成就徽章
  const achievements = useMemo(() => [
    { id: 'first', name: '首程启航', desc: '完成第一次行程规划', unlock: tripStats.total >= 1, icon: MapPin },
    { id: 'city3', name: '城市漫游者', desc: '足迹覆盖 3 座以上城市', unlock: tripStats.cityCount >= 3, icon: Compass },
    { id: 'share', name: '分享先锋', desc: '向同行者社区分享行程', unlock: myPosts.length >= 1, icon: Send },
    { id: 'chronicle', name: '旅途编年史', desc: '累计规划 5 趟以上行程', unlock: tripStats.total >= 5, icon: BookOpen },
    { id: 'explorer', name: '深度探索者', desc: '足迹覆盖 5 座以上城市', unlock: tripStats.cityCount >= 5, icon: Footprints }
  ], [tripStats, myPosts]);

  const handlePublishTrip = async (trip: ProfileTrip) => {
    setPublishedTripId(trip.ID);
    try {
      const res = await fetch(`${API_BASE}/api/community/post`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, title: trip.Title, content: trip.Content, dest_city: trip.DestCity })
      });
      const raw = await res.text();
      let data: any = null;
      try { data = JSON.parse(raw); } catch { throw new Error(`发布失败 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '发布失败');
    } catch (e: any) {
      setErrorMsg(e.message || '发布失败');
    }
  };

  return (
    <div className="min-h-screen w-full bg-[#f8f5ef] font-sans relative overflow-hidden">
      <div className="absolute top-[-12%] right-[-8%] w-[45%] h-[45%] rounded-full bg-amber-200/30 blur-[130px] pointer-events-none" />
      <div className="absolute bottom-[-10%] left-[-8%] w-[38%] h-[38%] rounded-full bg-orange-100/40 blur-[120px] pointer-events-none" />

      <div className="relative z-10 max-w-[980px] mx-auto px-4 sm:px-8 py-8">
        {/* 顶部导航 */}
        <button onClick={onBack} className="mb-7 inline-flex items-center gap-2 px-4 py-2 bg-white/80 border border-stone-200 rounded-full text-xs font-semibold text-stone-500 hover:text-amber-700 hover:border-amber-300 transition-colors shadow-sm cursor-pointer">
          <ArrowLeft className="w-4 h-4" /> 返回大厅
        </button>

        {/* 探路者档案 Hero */}
        <section className="relative overflow-hidden rounded-[28px] border border-amber-900/10 bg-[#fffdf8] shadow-[0_1px_0_rgba(0,0,0,0.02),0_24px_48px_-32px_rgba(146,90,28,0.35)] mb-7">
          <div className="h-1.5 w-full bg-gradient-to-r from-amber-600 via-orange-500 to-amber-600" />
          <div className="p-7 sm:p-9">
            <div className="flex flex-col lg:flex-row gap-8">
              <div className="shrink-0 flex flex-col items-center gap-3">
                <div className="relative">
                  <div className="w-28 h-28 rounded-[26px] overflow-hidden ring-1 ring-amber-900/10 shadow-lg bg-amber-50">
                    <img src={avatarUrl ? `${API_BASE}${avatarUrl}` : `https://api.dicebear.com/9.x/notionists/svg?seed=${encodeURIComponent(avatarSeed)}&backgroundColor=fdeed8`} alt="avatar" className="w-full h-full object-cover" />
                  </div>
                  <span className="absolute -bottom-2 -right-2 w-8 h-8 rounded-full bg-[#fffdf8] border border-amber-900/10 shadow flex items-center justify-center text-amber-600">
                    <Compass className="w-4 h-4" />
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={handleRandomAvatar} className="p-2 bg-amber-50 border border-amber-200/70 rounded-xl text-amber-700 hover:bg-amber-100 transition-colors cursor-pointer" title="随机换一个头像">
                    <Shuffle className="w-4 h-4" />
                  </button>
                  <button onClick={handleSaveAvatar} disabled={avatarSaving} className="p-2 bg-amber-50 border border-amber-200/70 rounded-xl text-amber-700 hover:bg-amber-100 transition-colors cursor-pointer disabled:opacity-50" title="保存头像">
                    {avatarSaved ? <Check className="w-4 h-4" /> : avatarSaving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  </button>
                  <button onClick={() => setShowUploader(true)} className="p-2 bg-amber-600 text-white rounded-xl hover:bg-amber-700 transition-colors cursor-pointer shadow-sm shadow-amber-200/50" title="上传自定义头像">
                    <Camera className="w-4 h-4" />
                  </button>
                </div>
                <input
                  type="text"
                  value={avatarSeed}
                  onChange={(e) => setAvatarSeed(e.target.value)}
                  placeholder="个性形象种子"
                  className="w-36 px-3 py-2 bg-amber-50/60 border border-amber-100 rounded-xl text-[11px] font-mono text-stone-600 placeholder-stone-400 outline-none focus:bg-white focus:border-amber-300 transition-all text-center"
                />
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2.5 mb-1">
                  <h2 className="font-serif text-3xl font-bold tracking-tight text-stone-900">{currentUser.nickname}</h2>
                  <span className="text-[10px] tracking-[0.18em] uppercase text-amber-700 bg-amber-100/70 border border-amber-200 rounded-full px-2.5 py-1 font-semibold">探路者档案</span>
                </div>
                <p className="text-sm text-stone-400 font-medium">@{currentUser.username}</p>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button onClick={() => { setEditNickname(currentUser.nickname || currentUser.username); setEditSignature(signature); setShowEditor(true); }} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-amber-50 border border-amber-200 text-amber-700 text-xs font-bold hover:bg-amber-100 transition-colors cursor-pointer">
                    <Edit3 className="w-3.5 h-3.5" /> 编辑资料
                  </button>
                  <button onClick={() => setShowUploader(true)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white border border-stone-200 text-stone-600 text-xs font-bold hover:border-amber-300 hover:text-amber-700 transition-colors cursor-pointer">
                    <Camera className="w-3.5 h-3.5" /> 上传头像
                  </button>
                </div>

                <div className="mt-5 flex items-start gap-2.5">
                  <Quote className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                  <p className="font-serif italic text-[15px] leading-relaxed text-stone-600">
                    {signature || (tripStats.total > 0 ? `已披阅 ${tripStats.cityCount} 座城市的风景，把每一次远行都写进了手账。` : '世界是一本摊开的书，从第一座城开始落笔。')}
                  </p>
                </div>

                <div className="mt-6 flex flex-wrap items-center gap-x-10 gap-y-4">
                  <div>
                    <span className="font-serif text-3xl font-bold text-stone-900">{tripStats.total}</span>
                    <span className="ml-2 text-xs font-medium text-stone-400">趟旅程</span>
                  </div>
                  <div className="hidden sm:block w-px h-9 bg-stone-200" />
                  <div>
                    <span className="font-serif text-3xl font-bold text-stone-900">{tripStats.cityCount}</span>
                    <span className="ml-2 text-xs font-medium text-stone-400">座城市</span>
                  </div>
                  <div className="hidden sm:block w-px h-9 bg-stone-200" />
                  <div>
                    <span className="font-serif text-3xl font-bold text-stone-900">{myPosts.length}</span>
                    <span className="ml-2 text-xs font-medium text-stone-400">条分享</span>
                  </div>
                </div>

                {tripStats.cities.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-6">
                    {tripStats.cities.slice(0, 6).map(c => (
                      <span key={c} className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-stone-600 bg-stone-100/80 border border-stone-200/60 px-2.5 py-1 rounded-full">
                        <MapPin className="w-3 h-3 text-amber-600" /> {c}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* 旅行偏好 */}
        <section className="rounded-[24px] border border-stone-200/80 bg-white/70 backdrop-blur p-6 sm:p-7 mb-6">
          <div className="flex items-center gap-3 mb-5">
            <div className="p-2.5 bg-amber-50 rounded-xl text-amber-600"><Compass className="w-5 h-5" /></div>
            <div>
              <h3 className="font-serif text-lg font-bold text-stone-900">旅行偏好</h3>
              <p className="text-xs text-stone-400">由你的历史行程节点自动勾勒的兴趣画像</p>
            </div>
          </div>
          {interestTags.length === 0 ? (
            <p className="text-sm text-stone-400 py-2">生成第一段行程后，这里会自动显形你的足迹偏好。</p>
          ) : (
            <div className="flex flex-wrap gap-2.5">
              {interestTags.map(([tag, count]) => (
                <span key={tag} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold text-stone-700 bg-white border border-stone-200 shadow-sm">
                  {tag.includes('美食') || tag.includes('餐') || tag.includes('小吃') ? <UtensilsCrossed className="w-3.5 h-3.5 text-orange-500" />
                    : tag.includes('风景') || tag.includes('山') || tag.includes('公园') || tag.includes('自然') ? <Mountain className="w-3.5 h-3.5 text-emerald-500" />
                    : tag.includes('文化') || tag.includes('历史') || tag.includes('博物') || tag.includes('古迹') ? <Landmark className="w-3.5 h-3.5 text-rose-500" />
                    : <Star className="w-3.5 h-3.5 text-amber-500" />}
                  {tag}
                  <span className="text-[10px] text-stone-400 font-bold">{count}</span>
                </span>
              ))}
            </div>
          )}
        </section>

        {/* 探索成就 */}
        <section className="rounded-[24px] border border-stone-200/80 bg-white/70 backdrop-blur p-6 sm:p-7 mb-6">
          <div className="flex items-center gap-3 mb-5">
            <div className="p-2.5 bg-amber-50 rounded-xl text-amber-600"><Trophy className="w-5 h-5" /></div>
            <div>
              <h3 className="font-serif text-lg font-bold text-stone-900">探索成就</h3>
              <p className="text-xs text-stone-400">每一次出发，都是一枚新的印记</p>
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {achievements.map((a) => {
              const Icon = a.icon;
              return (
                <div key={a.id} className={`relative overflow-hidden rounded-2xl border p-4 flex items-start gap-3 ${a.unlock ? 'bg-gradient-to-br from-amber-50 to-white border-amber-200/70' : 'bg-stone-50 border-stone-200/60'}`}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${a.unlock ? 'bg-amber-100 text-amber-700' : 'bg-stone-200/70 text-stone-400'}`}>
                    <Icon className="w-5 h-5" />
                  </div>
                  <div className="min-w-0">
                    <p className={`text-sm font-bold ${a.unlock ? 'text-stone-900' : 'text-stone-400'}`}>{a.name}</p>
                    <p className="text-[11px] text-stone-400 mt-0.5 leading-snug">{a.desc}</p>
                  </div>
                  {a.unlock ? <CheckCircle2 className="w-4 h-4 text-amber-500 absolute top-3 right-3" /> : <Lock className="w-3.5 h-3.5 text-stone-300 absolute top-3 right-3" />}
                </div>
              );
            })}
          </div>
        </section>

        {/* 旅行手账 */}
        <section className="rounded-[24px] border border-stone-200/80 bg-white/70 backdrop-blur p-6 sm:p-7 mb-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 bg-amber-50 rounded-xl text-amber-600"><BookOpen className="w-5 h-5" /></div>
            <div>
              <h3 className="font-serif text-lg font-bold text-stone-900">旅行手账</h3>
              <p className="text-xs text-stone-400">多智能体共识生成的每一条路书，都沉淀在这里</p>
            </div>
          </div>

          {loading ? (
            <div className="flex justify-center py-14"><RefreshCw className="w-6 h-6 text-amber-500 animate-spin" /></div>
          ) : errorMsg ? (
            <div className="p-4 bg-rose-50 border border-rose-200 rounded-2xl text-xs text-rose-600 font-bold">{errorMsg}</div>
          ) : trips.length === 0 ? (
            <div className="text-center py-14">
              <div className="w-14 h-14 mx-auto mb-4 bg-stone-100 rounded-full flex items-center justify-center text-stone-300"><BookOpen className="w-7 h-7" /></div>
              <p className="text-sm font-bold text-stone-500">手账还是一片空白</p>
              <p className="text-xs text-stone-400 font-medium mt-1.5">回到大厅开启一次协同推演，生成的路书即可写入手账</p>
            </div>
          ) : (
            <div className="space-y-3">
              {trips.map((trip) => {
                const routes = parseRoutes(trip.Content);
                const isOpen = expandedTrip === trip.ID;
                return (
                  <div key={trip.ID} className="border border-stone-200 rounded-2xl overflow-hidden bg-white/80">
                    <button onClick={() => setExpandedTrip(isOpen ? null : trip.ID)} className="w-full flex items-center justify-between p-4 hover:bg-amber-50/40 transition-colors cursor-pointer">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="p-2.5 bg-amber-50 rounded-xl text-amber-600 shrink-0"><MapPin className="w-5 h-5" /></div>
                        <div className="text-left min-w-0">
                          <p className="font-serif text-sm font-bold text-stone-800 truncate">{trip.Title || '未命名行程'}</p>
                          <p className="text-[11px] text-stone-400 font-medium flex items-center gap-2 mt-0.5">
                            <CalendarDays className="w-3 h-3" /> {formatDate(trip.CreatedAt)}{trip.DestCity ? ` · ${trip.DestCity}` : ''}
                          </p>
                        </div>
                      </div>
                      {isOpen ? <ChevronUp className="w-4 h-4 text-stone-400 shrink-0" /> : <ChevronDown className="w-4 h-4 text-stone-400 shrink-0" />}
                    </button>

                    {isOpen && (
                      <div className="px-4 pb-4 pt-1 border-t border-stone-100">
                        <button 
                          onClick={() => handlePublishTrip(trip)} 
                          disabled={publishedTripId === trip.ID}
                          className={`mt-3 w-full flex items-center justify-center gap-1.5 py-2 rounded-xl text-xs font-bold border transition-colors cursor-pointer ${publishedTripId === trip.ID ? 'bg-emerald-50 text-emerald-600 border-emerald-200' : 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100'}`}
                        >
                          <Send className="w-3.5 h-3.5" /> {publishedTripId === trip.ID ? '已分享到社区' : '分享到社区'}
                        </button>
                        {routes.length === 0 ? (
                          <p className="text-xs text-stone-400 py-3">该行程暂无结构化路线数据</p>
                        ) : (
                          <div className="space-y-2 mt-3">
                            {routes.map((r: any, i: number) => (
                              <div key={i} className="flex items-start gap-3 p-3 bg-stone-50 rounded-xl">
                                <span className="w-6 h-6 rounded-lg bg-white border border-stone-200 text-[11px] font-black text-amber-600 flex items-center justify-center shrink-0">D{r.day || '-'}</span>
                                <div className="min-w-0 flex-1">
                                  <p className="text-xs font-bold text-stone-700">{r.name || '地点'}</p>
                                  <p className="text-[10px] text-stone-400 mt-0.5 truncate">{r.time || ''}{r.desc ? ` · ${r.desc}` : ''}</p>
                                </div>
                                {r.cost && <span className="text-[10px] font-bold text-stone-500 shrink-0">{r.cost}</span>}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* 社交动态 */}
        <section className="rounded-[24px] border border-stone-200/80 bg-white/70 backdrop-blur p-6 sm:p-7 mb-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-2.5 bg-amber-50 rounded-xl text-amber-600"><MessageCircle className="w-5 h-5" /></div>
            <div>
              <h3 className="font-serif text-lg font-bold text-stone-900">沿途回声</h3>
              <p className="text-xs text-stone-400">你发布到同行者社区的分享与互动动态</p>
            </div>
          </div>

          {socialLoading ? (
            <div className="flex justify-center py-10"><RefreshCw className="w-6 h-6 text-amber-500 animate-spin" /></div>
          ) : myPosts.length === 0 ? (
            <div className="text-center py-10">
              <div className="w-14 h-14 mx-auto mb-4 bg-stone-100 rounded-full flex items-center justify-center text-stone-300"><MessageCircle className="w-7 h-7" /></div>
              <p className="text-sm font-bold text-stone-500">还没有分享过行程</p>
              <p className="text-xs text-stone-400 font-medium mt-1.5">在「旅行手账」中将满意路书分享到社区，即可在这里看到</p>
            </div>
          ) : (
            <div className="space-y-3">
              {myPosts.map((post) => (
                <div key={post.id} className="border border-stone-200 rounded-2xl p-4 bg-white/80 hover:shadow-md hover:border-amber-200 transition-all">
                  <div className="flex items-center gap-2 flex-wrap mb-1.5">
                    <h4 className="font-serif text-sm font-bold text-stone-800">{post.title || '分享了一条行程'}</h4>
                    {post.dest_city && <span className="text-[10px] bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full font-bold">{post.dest_city}</span>}
                    <span className="text-[10px] text-stone-400 font-medium ml-auto">{formatDate(post.created_at)}</span>
                  </div>
                  <p className="text-xs text-stone-500 leading-relaxed">{summarizeTrip(post.content)}</p>
                  <div className="flex items-center gap-4 mt-3 pt-2.5 border-t border-stone-100">
                    <span className="flex items-center gap-1.5 text-xs font-bold text-stone-400"><Heart className="w-3.5 h-3.5" /> {post.likes}</span>
                    <span className="flex items-center gap-1.5 text-xs font-bold text-stone-400"><MessageCircle className="w-3.5 h-3.5" /> {post.comments}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {showUploader && (
        <AvatarUploader userId={currentUser.id} onClose={() => setShowUploader(false)} onSuccess={handleAvatarUploaded} />
      )}

      {showEditor && (
        <AnimatePresence>
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-stone-900/30 backdrop-blur-sm z-[120] flex items-center justify-center p-4">
            <motion.div initial={{ scale: 0.95, y: 15 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: -15 }} className="bg-[#fffdf8] rounded-[28px] w-full max-w-md p-7 shadow-2xl border border-amber-900/10 relative">
              <button onClick={() => setShowEditor(false)} className="absolute top-5 right-5 p-1.5 rounded-full hover:bg-stone-100 text-stone-400 hover:text-stone-600 transition-colors cursor-pointer"><X className="w-5 h-5" /></button>
              <h3 className="font-serif text-lg font-bold text-stone-900 mb-1">编辑个人资料</h3>
              <p className="text-xs text-stone-400 mb-5">完善你的探路者档案，让同行者更了解你</p>

              <div className="space-y-4">
                <div>
                  <label className="text-xs font-bold text-stone-600 mb-1.5 block">昵称</label>
                  <input type="text" value={editNickname} onChange={(e) => setEditNickname(e.target.value)} placeholder="你的昵称" className="w-full px-3 py-2.5 bg-stone-50 border border-stone-200 rounded-xl text-xs outline-none focus:border-amber-500 focus:bg-white font-medium transition-colors" />
                </div>
                <div>
                  <label className="text-xs font-bold text-stone-600 mb-1.5 block">个性签名</label>
                  <textarea rows={3} value={editSignature} onChange={(e) => setEditSignature(e.target.value)} placeholder="写一句与你同行、与远方有关的话..." className="w-full p-3 bg-stone-50 border border-stone-200 rounded-xl text-xs outline-none focus:border-amber-500 focus:bg-white resize-none font-medium leading-relaxed transition-colors" />
                </div>
                <button onClick={handleSaveProfile} disabled={profileSaving} className="w-full py-3 bg-amber-600 hover:bg-amber-700 disabled:bg-stone-200 disabled:text-stone-400 text-white rounded-xl text-sm font-bold transition-colors cursor-pointer flex items-center justify-center gap-2">
                  {profileSaving ? <><RefreshCw className="w-4 h-4 animate-spin" /> 保存中...</> : <><Save className="w-4 h-4" /> 保存资料</>}
                </button>
              </div>
            </motion.div>
          </motion.div>
        </AnimatePresence>
      )}
    </div>
  );
}

// =========================================================================
// 社区：用户分享行程/智能体规划，他人点赞、评论、回复
// =========================================================================
interface CommunityPostItem {
  id: string;
  user_id: string;
  author: string;
  author_avatar: string;
  author_avatar_url: string;
  title: string;
  content: string;
  dest_city: string;
  likes: number;
  comments: number;
  created_at: string;
}
interface CommunityComment {
  id: string;
  post_id: string;
  user_id: string;
  author: string;
  author_avatar: string;
  author_avatar_url: string;
  content: string;
  parent_id: string;
  created_at: string;
}

function CommunityScreen({ currentUser, onBack }: { currentUser: UserProfile; onBack: () => void }) {
  const [posts, setPosts] = useState<CommunityPostItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState('');
  const [showComposer, setShowComposer] = useState(false);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [city, setCity] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [expandedRoute, setExpandedRoute] = useState<string | null>(null);
  const [commentsMap, setCommentsMap] = useState<Record<string, CommunityComment[]>>({});
  const [commentText, setCommentText] = useState('');
  const [replyingTo, setReplyingTo] = useState<CommunityComment | null>(null);
  const [likeLoading, setLikeLoading] = useState<string | null>(null);

  const fetchPosts = async () => {
    setLoading(true);
    setErrorMsg('');
    try {
      const res = await fetch(`${API_BASE}/api/community/list`);
      const raw = await res.text();
      let data: any = null; try { data = JSON.parse(raw); } catch { throw new Error(`社区响应异常 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '社区加载失败');
      setPosts(data.posts || []);
    } catch (e: any) { setErrorMsg(e.message || '社区加载失败'); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchPosts(); }, []);

  const toggleComments = async (postId: string) => {
    if (expanded === postId) { setExpanded(null); return; }
    setExpanded(postId);
    if (!commentsMap[postId]) {
      try {
        const res = await fetch(`${API_BASE}/api/community/comments?post_id=${postId}`);
        const raw = await res.text();
        let data: any = null; try { data = JSON.parse(raw); } catch { return; }
        setCommentsMap(prev => ({ ...prev, [postId]: data.comments || [] }));
      } catch {}
    }
  };

  const handlePublish = async () => {
    setPublishing(true);
    try {
      const res = await fetch(`${API_BASE}/api/community/post`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, title: title.trim(), content: content.trim(), dest_city: city.trim() })
      });
      const raw = await res.text();
      let data: any = null; try { data = JSON.parse(raw); } catch { throw new Error(`发布失败 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '发布失败');
      setShowComposer(false); setTitle(''); setContent(''); setCity('');
      fetchPosts();
    } catch (e: any) { setErrorMsg(e.message || '发布失败'); }
    finally { setPublishing(false); }
  };

  const handleLike = async (postId: string) => {
    setLikeLoading(postId);
    try {
      const res = await fetch(`${API_BASE}/api/community/like`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ post_id: postId })
      });
      const raw = await res.text();
      let data: any = null; try { data = JSON.parse(raw); } catch { return; }
      if (res.ok && typeof data.likes === 'number') {
        setPosts(prev => prev.map(p => p.id === postId ? { ...p, likes: data.likes } : p));
      }
    } catch {} finally { setLikeLoading(null); }
  };

  const handleSendComment = async (postId: string) => {
    const text = commentText.trim();
    if (!text) return;
    try {
      const res = await fetch(`${API_BASE}/api/community/comment`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ post_id: postId, user_id: currentUser.id, content: text, parent_id: replyingTo?.id || '' })
      });
      const raw = await res.text();
      let data: any = null; try { data = JSON.parse(raw); } catch { return; }
      if (!res.ok) throw new Error(data?.error || '评论失败');
      setCommentText(''); setReplyingTo(null);
      const r2 = await fetch(`${API_BASE}/api/community/comments?post_id=${postId}`);
      const raw2 = await r2.text();
      let d2: any = null; try { d2 = JSON.parse(raw2); } catch { return; }
      setCommentsMap(prev => ({ ...prev, [postId]: d2.comments || [] }));
      setPosts(prev => prev.map(p => p.id === postId ? { ...p, comments: p.comments + 1 } : p));
    } catch (e: any) { setErrorMsg(e.message || '评论失败'); }
  };

  const formatDate = (s: string) => {
    if (!s) return '';
    const d = new Date(s);
    return isNaN(d.getTime()) ? s : d.toLocaleDateString('zh-CN');
  };

  const parsePostContent = (c: string): { summary?: string; city?: string; days?: number[]; routes?: any[] } | null => {
    try {
      const obj = JSON.parse(c);
      return (obj && typeof obj === 'object') ? obj : null;
    } catch { return null; }
  };

  const renderContent = (c: string) => {
    const parsed = parsePostContent(c);
    if (parsed && typeof parsed.summary === 'string' && parsed.summary) return parsed.summary;
    if (parsed && Array.isArray(parsed.routes) && parsed.routes.length > 0) {
      return `已规划 ${parsed.routes.length} 个行程节点${parsed.city ? ` · 目的地 ${parsed.city}` : ''}`;
    }
    return c;
  };

  return (
    <div className="min-h-screen w-full bg-[#f8f5ef] font-sans relative overflow-hidden">
      <div className="absolute top-[-12%] right-[-8%] w-[42%] h-[42%] rounded-full bg-amber-200/30 blur-[130px] pointer-events-none" />
      <div className="absolute bottom-[-10%] left-[-8%] w-[36%] h-[36%] rounded-full bg-orange-100/40 blur-[120px] pointer-events-none" />
      <div className="relative z-10 max-w-[860px] mx-auto px-4 sm:px-8 py-8">
        {/* 顶部导航 */}
        <div className="flex items-center justify-between mb-6">
          <button onClick={onBack} className="inline-flex items-center gap-2 px-4 py-2 bg-white/80 border border-stone-200 rounded-full text-xs font-semibold text-stone-500 hover:text-amber-700 hover:border-amber-300 transition-colors shadow-sm cursor-pointer">
            <ArrowLeft className="w-4 h-4" /> 返回大厅
          </button>
          <button onClick={() => setShowComposer(true)} className="inline-flex items-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-full text-xs font-bold hover:bg-amber-700 shadow-md shadow-amber-200/60 transition-colors cursor-pointer">
            <Plus className="w-4 h-4" /> 发布分享
          </button>
        </div>

        {/* 标题 */}
        <div className="flex items-center gap-3 mb-6">
          <div className="p-2.5 bg-amber-50 rounded-xl text-amber-600"><Flame className="w-5 h-5" /></div>
          <div>
            <h2 className="font-serif text-2xl font-bold tracking-tight text-stone-900">同行者社区</h2>
            <p className="text-xs text-stone-400 font-medium">把满意的路书留在沿途，让后来者与智能体从中听见回声</p>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center py-20"><RefreshCw className="w-6 h-6 text-amber-500 animate-spin" /></div>
        ) : errorMsg ? (
          <div className="p-4 bg-rose-50 border border-rose-200 rounded-2xl text-xs text-rose-600 font-bold">{errorMsg}</div>
        ) : posts.length === 0 ? (
          <div className="text-center py-20 bg-white/70 border border-stone-200/80 rounded-[24px]">
            <div className="w-14 h-14 mx-auto mb-4 bg-stone-100 rounded-full flex items-center justify-center text-stone-300"><MessageCircle className="w-7 h-7" /></div>
            <p className="font-serif text-sm font-bold text-stone-500">社区还没有帖子</p>
            <p className="text-xs text-stone-400 font-medium mt-1.5">成为第一个分享行程的人吧</p>
          </div>
        ) : (
          <div className="space-y-4">
            {posts.map((post) => {
              const isOpen = expanded === post.id;
              const comments = commentsMap[post.id] || [];
              const topLevel = comments.filter(c => !c.parent_id);
              const parsed = parsePostContent(post.content);
              const routeNodes = (parsed && Array.isArray(parsed.routes)) ? parsed.routes : [];
              return (
                <div key={post.id} className="bg-white/70 backdrop-blur border border-stone-200/80 rounded-[24px] p-5 sm:p-6 shadow-sm hover:shadow-md hover:border-amber-200/70 transition-all">
                  <div className="flex items-start gap-3">
                    <img src={post.author_avatar_url ? `${API_BASE}${post.author_avatar_url}` : `https://api.dicebear.com/9.x/notionists/svg?seed=${encodeURIComponent(post.author_avatar || post.author)}&backgroundColor=fdeed8`} alt="avatar" className="w-10 h-10 rounded-full bg-amber-50 border border-amber-900/10 shrink-0 object-cover" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-serif text-sm font-bold text-stone-800">{post.author}</span>
                        {post.dest_city && <span className="inline-flex items-center gap-1 text-[10px] bg-amber-50 text-amber-700 px-2 py-0.5 rounded-full font-bold"><MapPin className="w-3 h-3" />{post.dest_city}</span>}
                        <span className="text-[10px] text-stone-400 font-medium ml-auto">{formatDate(post.created_at)}</span>
                      </div>
                      <h3 className="font-serif text-base font-bold text-stone-800 mt-1.5">{post.title || '分享了一条行程'}</h3>
                      <p className="text-xs text-stone-500 leading-relaxed mt-1 font-medium">{renderContent(post.content)}</p>
                    </div>
                  </div>

                  {routeNodes.length > 0 && (
                    <div className="mt-3">
                      <button
                        onClick={() => setExpandedRoute(expandedRoute === post.id ? null : post.id)}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-50 text-amber-700 border border-amber-200 text-xs font-bold hover:bg-amber-100 transition-colors cursor-pointer"
                      >
                        {expandedRoute === post.id ? <><ChevronUp className="w-3.5 h-3.5" /> 收起完整路书</> : <><Route className="w-3.5 h-3.5" /> 展开完整路书</>}
                      </button>

                      {expandedRoute === post.id && (
                        <div className="mt-3 rounded-2xl border border-stone-200/80 bg-stone-50/70 p-4 space-y-4 max-h-80 overflow-y-auto custom-scrollbar">
                          {routeNodes.map((node: any, ni: number) => {
                            const showDayHeader = ni === 0 || (node.day != null && node.day !== routeNodes[ni - 1]?.day);
                            return (
                              <div key={`route-node-${post.id}-${ni}`}>
                                {showDayHeader && (
                                  <div className="flex items-center gap-2 mb-2">
                                    <span className="text-[11px] font-black text-amber-700 bg-amber-100 px-2 py-0.5 rounded-md">第 {node.day || ''} 天</span>
                                  </div>
                                )}
                                <div className="flex items-start gap-2.5">
                                  <span className="w-5 h-5 mt-0.5 rounded-full bg-white border border-stone-200 text-[10px] font-black text-amber-600 flex items-center justify-center shrink-0">{ni + 1}</span>
                                  <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <span className="text-xs font-bold text-stone-700">{node.name || '地点'}</span>
                                      {node.time && <span className="text-[10px] text-stone-400">{(node.time.split('|')[1] || node.time).trim()}</span>}
                                      {node.cost && <span className="text-[10px] font-bold text-emerald-600">{node.cost}</span>}
                                    </div>
                                    {node.desc && <p className="text-[11px] text-stone-500 leading-relaxed mt-0.5">{node.desc}</p>}
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}

                  <div className="flex items-center gap-4 mt-4 pt-3 border-t border-stone-100">
                    <button onClick={() => handleLike(post.id)} disabled={likeLoading === post.id} className="flex items-center gap-1.5 text-xs font-bold text-stone-400 hover:text-rose-500 transition-colors cursor-pointer disabled:opacity-60">
                      <Heart className="w-4 h-4" /> {post.likes}
                    </button>
                    <button onClick={() => toggleComments(post.id)} className="flex items-center gap-1.5 text-xs font-bold text-stone-400 hover:text-amber-600 transition-colors cursor-pointer">
                      <MessageCircle className="w-4 h-4" /> {post.comments}
                    </button>
                  </div>

                  {isOpen && (
                    <div className="mt-3 pt-3 border-t border-stone-100">
                      <div className="space-y-3 mb-3">
                        {topLevel.length === 0 && <p className="text-[11px] text-stone-400">还没有评论，来说点什么吧~</p>}
                        {topLevel.map((cm) => {
                          const replies = comments.filter(c => c.parent_id === cm.id);
                          return (
                            <div key={cm.id} className="text-xs">
                              <div className="flex items-start gap-2">
                                <img src={cm.author_avatar_url ? `${API_BASE}${cm.author_avatar_url}` : `https://api.dicebear.com/9.x/notionists/svg?seed=${encodeURIComponent(cm.author_avatar || cm.author)}&backgroundColor=fdeed8`} alt="" className="w-6 h-6 rounded-full shrink-0 object-cover" />
                                <div className="min-w-0 flex-1">
                                  <p className="font-bold text-stone-700">{cm.author} <span className="font-normal text-stone-500 ml-1">{cm.content}</span></p>
                                  <button onClick={() => { setReplyingTo(cm); setCommentText(`@${cm.author} `); }} className="text-[10px] text-stone-400 hover:text-amber-600 mt-0.5 cursor-pointer">回复</button>
                                </div>
                              </div>
                              {replies.length > 0 && (
                                <div className="ml-7 mt-1.5 space-y-1.5">
                                  {replies.map((rp) => (
                                    <div key={rp.id} className="flex items-start gap-2">
                                      <img src={rp.author_avatar_url ? `${API_BASE}${rp.author_avatar_url}` : `https://api.dicebear.com/9.x/notionists/svg?seed=${encodeURIComponent(rp.author_avatar || rp.author)}&backgroundColor=fdeed8`} alt="" className="w-5 h-5 rounded-full shrink-0 object-cover" />
                                      <p className="text-[11px] text-stone-600"><span className="font-bold">{rp.author}</span> {rp.content}</p>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                      <div className="flex items-center gap-2">
                        <input
                          type="text"
                          value={commentText}
                          onChange={(e) => setCommentText(e.target.value)}
                          placeholder={replyingTo ? `回复 ${replyingTo.author}` : '写下你的评论...'}
                          className="flex-1 px-3 py-2 bg-stone-50 border border-stone-200 rounded-xl text-xs outline-none focus:border-amber-500 focus:bg-white font-medium text-stone-700 transition-colors"
                        />
                        <button onClick={() => handleSendComment(post.id)} className="p-2 bg-amber-600 text-white rounded-xl hover:bg-amber-700 transition-colors cursor-pointer" title="发送评论">
                          <Send className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 发帖弹窗 */}
      <AnimatePresence>
        {showComposer && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-stone-900/30 backdrop-blur-sm z-[120] flex items-center justify-center p-4">
            <motion.div initial={{ scale: 0.95, y: 15 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: -15 }} className="bg-[#fffdf8] rounded-[28px] w-full max-w-md p-7 shadow-2xl border border-amber-900/10 relative">
              <button onClick={() => setShowComposer(false)} className="absolute top-5 right-5 p-1.5 rounded-full hover:bg-stone-100 text-stone-400 hover:text-stone-600 transition-colors cursor-pointer"><X className="w-5 h-5" /></button>
              <h3 className="font-serif text-lg font-bold text-stone-900 mb-1">发布到社区</h3>
              <p className="text-xs text-stone-400 mb-5">分享满意的行程，让其他人评价与借鉴</p>

              <div className="space-y-4">
                <div>
                  <label className="text-xs font-bold text-stone-600 mb-1.5 block">标题</label>
                  <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如：洛阳 3 日深度游攻略" className="w-full px-3 py-2.5 bg-stone-50 border border-stone-200 rounded-xl text-xs outline-none focus:border-amber-500 focus:bg-white font-medium transition-colors" />
                </div>
                <div>
                  <label className="text-xs font-bold text-stone-600 mb-1.5 block">目的地城市</label>
                  <input type="text" value={city} onChange={(e) => setCity(e.target.value)} placeholder="例如：洛阳" className="w-full px-3 py-2.5 bg-stone-50 border border-stone-200 rounded-xl text-xs outline-none focus:border-amber-500 focus:bg-white font-medium transition-colors" />
                </div>
                <div>
                  <label className="text-xs font-bold text-stone-600 mb-1.5 block">行程描述 / 心得</label>
                  <textarea rows={4} value={content} onChange={(e) => setContent(e.target.value)} placeholder="写下这段行程的亮点、体验与建议..." className="w-full p-3 bg-stone-50 border border-stone-200 rounded-xl text-xs outline-none focus:border-amber-500 focus:bg-white resize-none font-medium leading-relaxed transition-colors" />
                </div>
                <button onClick={handlePublish} disabled={publishing || (!title.trim() && !content.trim())} className="w-full py-3 bg-amber-600 hover:bg-amber-700 disabled:bg-stone-200 disabled:text-stone-400 text-white rounded-xl text-sm font-bold transition-colors cursor-pointer flex items-center justify-center gap-2">
                  {publishing ? <><RefreshCw className="w-4 h-4 animate-spin" /> 发布中...</> : <><Send className="w-4 h-4" /> 发布分享</>}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// =========================================================================
// 主入口：认证判断 -> 大厅界面 -> 多智能体决策工作区
// =========================================================================
export default function ContextualLobby() {
  const [selectedMode, setSelectedMode] = useState<string>('coop');
  const [showPreferenceModal, setShowPreferenceModal] = useState(false);
  const [showEditIntentModal, setShowEditIntentModal] = useState(false);

  const [selectedRole, setSelectedRole] = useState<string>('寻味探索');
  const [myIntent, setMyIntent] = useState<string>('必须安排当地特色正宗老字号，不吃预制菜');
  const [roomCode, setRoomCode] = useState<string>('OMNI-88');
  const [inputRoomCode, setInputRoomCode] = useState<string>('');
  
  const [isConnecting, setIsConnecting] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [showCommunity, setShowCommunity] = useState(false);
  const [showDestinationMap, setShowDestinationMap] = useState(false);
  const [planDestination, setPlanDestination] = useState<string | null>(null);

  // 本地持久化用户状态 (与 SQLite 后端鉴权打通)
  // 改为在客户端挂载后再读取 localStorage，避免 SSR 与 CSR 首次渲染不一致导致 Hydration 报错
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [authReady, setAuthReady] = useState(false);

  // 房间真实成员列表（由 WebSocket 广播集中下发同步）
  const [roomMembers, setRoomMembers] = useState<RoomMember[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const [copied, setCopied] = useState(false);

  // 挂载后从 localStorage 恢复登录态（仅在浏览器端执行）
  useEffect(() => {
    try {
      const saved = localStorage.getItem('omni_user');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed && parsed.isLoggedIn) setCurrentUser(parsed);
      }
    } catch (e) {}
    setAuthReady(true);
  }, []);

  // WebSocket 维持房间连接与在线成员同步
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    const connectWs = () => {
      if (!isMounted || !currentUser) return;
      const url = `${WS_BASE}/ws?room_id=${encodeURIComponent(roomCode)}&user_id=${encodeURIComponent(currentUser.id)}&nickname=${encodeURIComponent(currentUser.nickname)}&role=${encodeURIComponent(selectedRole)}&intent=${encodeURIComponent(myIntent)}`;
      ws = new WebSocket(url);

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "room_members_update") {
            setRoomMembers(msg.payload || []);
          }
        } catch (err) {}
      };

      ws.onclose = () => {
        if (!isMounted) return;
        timer = setTimeout(connectWs, 3000);
      };
      wsRef.current = ws;
    };

    connectWs();

    return () => {
      isMounted = false;
      if (timer) clearTimeout(timer);
      if (ws) ws.close();
    };
  }, [roomCode, currentUser, selectedRole, myIntent]);

  // 认证状态尚未从 localStorage 恢复完成，先渲染占位避免 Hydration 不一致
  if (!authReady) {
    return (
      <div className="min-h-screen w-full bg-slate-50 flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-orange-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // 1. 若用户未登录，展示全屏登录注册门户
  if (!currentUser || !currentUser.isLoggedIn) {
    return (
      <AuthPortalScreen 
        onLoginSuccess={(user) => {
          const userState: UserProfile = { ...user, isLoggedIn: true };
          setCurrentUser(userState);
          localStorage.setItem('omni_user', JSON.stringify(userState));
        }} 
      />
    );
  }

  const handleBeginPlan = () => setShowPreferenceModal(true);

  const handleStartPlanFromDestination = (destination: string) => {
    setPlanDestination(destination);
    setShowDestinationMap(false);
    setShowPreferenceModal(true); // 复用现有「定制主控体验」流程
  };

  const handleConfirmSync = () => {
    setIsConnecting(true);
    setTimeout(() => {
      setIsConnecting(false);
      setShowPreferenceModal(false);
      setIsTransitioning(false);
      setShowWorkspace(true);
    }, 1000); 
  };

  const handleUpdateMyIntent = (newRole: string, newIntent: string) => {
    setSelectedRole(newRole);
    setMyIntent(newIntent);
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: "update_member",
        payload: { role: newRole, intent: newIntent }
      }));
    }
    setShowEditIntentModal(false);
  };

  const handleJoinRoom = () => {
    if (!inputRoomCode.trim()) return;
    setRoomCode(inputRoomCode.trim().toUpperCase());
    setInputRoomCode('');
  };

  const handleCopyRoomCode = async () => {
    try {
      await navigator.clipboard.writeText(roomCode);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {}
  };

  const handleLogout = () => {
    localStorage.removeItem('omni_user');
    setCurrentUser(null);
  };

  const renderRightPanel = () => {
    switch (selectedMode) {
      case 'solo':
        return (
          <motion.div key="solo-panel-mode" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.3 }} className="flex flex-col h-full">
            <div className="p-7 pb-5 border-b border-slate-200/60 bg-white/50">
              <h2 className="text-xl font-bold text-slate-800 mb-1 flex items-center gap-2"><Compass className="w-5 h-5 text-emerald-600"/> 私人探索雷达</h2>
              <p className="text-xs text-slate-500 font-medium">系统已屏蔽外界干扰，专注为您构建沉浸式路书</p>
            </div>
            <div className="p-6 flex-1 flex flex-col gap-4">
              <div className="p-4 rounded-xl bg-white border border-slate-100 shadow-sm flex items-start gap-4">
                <div className="p-2 bg-emerald-50 rounded-lg text-emerald-600"><Headphones className="w-5 h-5"/></div>
                <div>
                  <p className="text-sm font-bold text-slate-800">深度沉浸模式已就绪</p>
                  <p className="text-xs text-slate-500 mt-1">智能规避拥挤旅行团，提升小众秘境与人文地标的推荐权重。</p>
                </div>
              </div>
            </div>
          </motion.div>
        );
      case 'pvp':
        return (
          <motion.div key="pvp-panel-mode" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.3 }} className="flex flex-col h-full">
            <div className="p-7 pb-5 border-b border-slate-200/60 bg-white/50">
              <h2 className="text-xl font-bold text-slate-800 mb-1 flex items-center gap-2"><TrendingDown className="w-5 h-5 text-rose-600"/> 极客精算中枢</h2>
              <p className="text-xs text-slate-500 font-medium">底层算力全开，为您挖掘全网极致性价比组合</p>
            </div>
            <div className="p-6 flex-1 flex flex-col gap-4">
              <div className="p-4 rounded-xl bg-gradient-to-br from-rose-50/50 to-white border border-rose-100 shadow-sm flex items-center justify-between">
                <div>
                  <p className="text-xs font-bold text-slate-400 mb-1">当前测算状态</p>
                  <p className="text-sm font-bold text-slate-800 flex items-center gap-2">
                    <TrendingDown className="w-4 h-4 text-rose-500" /> 全网比价引擎就绪，进入推演后自动拉取
                  </p>
                </div>
              </div>
            </div>
          </motion.div>
        );
      case 'coop':
      default:
        return (
          <motion.div key="coop-panel-mode" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.3 }} className="flex flex-col h-full">
            <div className="p-7 pb-4 border-b border-slate-200/60 bg-white/50">
              <div className="flex items-center justify-between mb-1">
                <h2 className="text-xl font-bold text-slate-800">同行动态网络 ({roomMembers.length}人在线)</h2>
                <span className="text-[10px] bg-emerald-50 text-emerald-600 font-bold px-2 py-0.5 rounded-full border border-emerald-200">WebSocket 实时协同</span>
              </div>
              <p className="text-xs text-slate-500 mb-3 font-medium">基于真实房间号广播，多人跨屏协同博弈</p>
              
              <div className="bg-white rounded-xl p-3 border border-slate-200 shadow-sm mb-1">
                <div className="flex justify-between items-center mb-1.5">
                  <span className="text-xs font-bold text-slate-500">当前协作房间码（分享给好友即可同行）</span>
                  <span className="text-base font-black text-orange-600 font-mono tracking-wider flex items-center gap-1.5">
                    {roomCode}
                    <button onClick={handleCopyRoomCode} className="p-1 hover:bg-orange-50 rounded-md transition-colors cursor-pointer" title="复制邀请码">
                      {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5 text-orange-500" />}
                    </button>
                    <button onClick={() => setRoomCode(Math.random().toString(36).substring(2, 8).toUpperCase())} className="p-1 hover:bg-orange-50 rounded-md transition-colors cursor-pointer" title="生成新房间码">
                      <RefreshCw className="w-3.5 h-3.5 text-orange-500" />
                    </button>
                  </span>
                </div>
                <div className="flex gap-1.5 mt-2">
                  <input 
                    type="text" 
                    placeholder="输入好友分享的房间码"
                    value={inputRoomCode}
                    onChange={(e) => setInputRoomCode(e.target.value)}
                    className="flex-1 px-3 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs outline-none focus:border-orange-500 font-mono uppercase font-bold text-slate-800"
                  />
                  <button onClick={handleJoinRoom} className="px-3.5 py-1.5 bg-slate-900 hover:bg-orange-500 text-white rounded-lg text-xs font-bold transition-colors cursor-pointer">
                    加入房间
                  </button>
                </div>
              </div>
            </div>

            <div className="p-5 flex-1 flex flex-col gap-2.5 overflow-y-auto custom-scrollbar">
              {roomMembers.map((member) => (
                <div 
                  key={member.id} 
                  className={`p-3 rounded-2xl flex items-center justify-between border transition-all ${member.id === currentUser.id ? 'bg-orange-50/80 border-orange-200 shadow-2xs' : 'bg-white border-slate-100'}`}
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <img src={`https://api.dicebear.com/9.x/avataaars/svg?seed=${member.avatarSeed || member.name}&backgroundColor=b6e3f4`} alt="avatar" className="w-9 h-9 rounded-full bg-slate-100 border border-slate-200 shrink-0" />
                    <div className="overflow-hidden">
                      <div className="flex items-center gap-1.5">
                        <p className="text-xs font-bold text-slate-800 truncate">{member.name} {member.id === currentUser.id && '(我)'}</p>
                        <span className="text-[9px] text-orange-600 bg-orange-100/70 px-1.5 py-0.5 rounded font-bold shrink-0">{member.role}</span>
                      </div>
                      <p className="text-[10px] text-slate-400 truncate mt-0.5">{member.intent}</p>
                    </div>
                  </div>
                  {member.id === currentUser.id && (
                    <button 
                      onClick={() => setShowEditIntentModal(true)} 
                      className="p-1 hover:bg-orange-100 text-orange-600 rounded-lg transition-colors cursor-pointer shrink-0 ml-2" 
                      title="调整我的诉求画像"
                    >
                      <Edit3 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </motion.div>
        );
    }
  };

  if (showWorkspace) {
    return (
      <UnifiedWorkspace 
        mode={selectedMode} 
        role={selectedRole} 
        roomCode={roomCode} 
        roomMembers={roomMembers}
        currentUser={currentUser}
        initialDestination={planDestination}
        onBack={() => setShowWorkspace(false)} 
      />
    );
  }

  if (showProfile) {
    return (
      <ProfileScreen 
        currentUser={currentUser}
        onBack={() => setShowProfile(false)}
        onProfileChange={(patch) => {
          const updated = { ...currentUser, ...patch };
          setCurrentUser(updated);
          localStorage.setItem('omni_user', JSON.stringify(updated));
        }}
      />
    );
  }

  if (showCommunity) {
    return (
      <CommunityScreen 
        currentUser={currentUser}
        onBack={() => setShowCommunity(false)}
      />
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 sm:p-8 font-sans bg-slate-50/50 relative overflow-hidden">
      <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] rounded-full bg-orange-400/10 blur-[120px] pointer-events-none"></div>
      <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] rounded-full bg-amber-400/10 blur-[120px] pointer-events-none"></div>

      <AnimatePresence>
        {!isTransitioning && (
          <motion.div 
            key="lobby-main-card-wrapper"
            initial={{ opacity: 0, scale: 0.98, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95, y: -20, filter: "blur(8px)" }} transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className="relative z-10 w-full max-w-[1240px] min-h-[85vh] bg-white/70 backdrop-blur-3xl border border-white shadow-[0_8px_40px_rgba(0,0,0,0.04)] rounded-[2rem] flex flex-col overflow-hidden"
          >
            <header className="px-10 py-5 flex justify-between items-center border-b border-slate-200/50 bg-white/40">
              <div className="flex items-center gap-4">
                <OmniLogo className="w-10 h-10 hover:scale-105 transition-transform" />
                <div>
                  <h1 className="text-2xl font-black text-slate-800 tracking-tight">OmniRoute</h1>
                  <p className="text-xs text-slate-500 font-bold tracking-widest uppercase mt-0.5">多智能体协同旅行中枢</p>
                </div>
              </div>
              
              <div className="flex items-center gap-4">
                <div className="hidden sm:flex items-center gap-2 px-3.5 py-1.5 bg-white border border-slate-100 rounded-full text-xs font-bold text-slate-600 shadow-sm">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                  多 Agent 协商黑板已就绪
                </div>

                <button 
                  onClick={() => setShowProfile(true)}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 bg-orange-50 border border-orange-200 text-orange-600 rounded-full text-xs font-bold hover:bg-orange-100 transition-colors cursor-pointer"
                >
                  <User className="w-3.5 h-3.5" /> 个人主页
                </button>

                <button 
                  onClick={() => setShowCommunity(true)}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 bg-blue-50 border border-blue-200 text-blue-600 rounded-full text-xs font-bold hover:bg-blue-100 transition-colors cursor-pointer"
                >
                  <MessageCircle className="w-3.5 h-3.5" /> 社区
                </button>

                <div className="flex items-center gap-3 bg-white border border-slate-200/80 px-3 py-1.5 rounded-2xl shadow-xs">
                  <div className="w-8 h-8 rounded-full overflow-hidden bg-orange-100 border border-orange-200">
                    <img src={`https://api.dicebear.com/9.x/avataaars/svg?seed=${currentUser.avatarSeed}&backgroundColor=b6e3f4`} alt="avatar" className="w-full h-full object-cover" />
                  </div>
                  <div className="text-left">
                    <span className="text-xs font-black text-slate-800 block">{currentUser.nickname}</span>
                    <span className="text-[10px] text-emerald-600 font-bold">SQLite 认证就绪</span>
                  </div>
                  <button 
                    onClick={handleLogout}
                    className="ml-1 p-1.5 hover:bg-rose-50 text-slate-400 hover:text-rose-500 rounded-lg transition-colors cursor-pointer"
                    title="退出登录"
                  >
                    <LogOut className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </header>

            <main className="flex-1 p-8 sm:p-10 grid grid-cols-1 lg:grid-cols-12 gap-8 overflow-y-auto overflow-x-hidden">
              <div className="lg:col-span-8 flex flex-col gap-6">
                <div>
                  <h2 className="text-3xl font-extrabold text-slate-800 mb-2 tracking-tight">开启下一段旅程协同推演</h2>
                  <p className="text-slate-500 text-sm font-medium">邀请好友输入相同房间号，所有人的专属智能体将实时汇聚在沙盘中博弈。</p>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <TravelModeCard id="solo" title="一人行" desc="步调全由自己掌控，定制绝对自由的私人路书。" icon={<MapPin className="w-6 h-6" />} color="text-emerald-600" selected={selectedMode === 'solo'} onClick={() => setSelectedMode('solo')} />
                  <TravelModeCard id="coop" title="亲友结伴" desc="告别众口难调，多智能体博弈平衡喜好与预算冲突。" icon={<Users className="w-6 h-6" />} color="text-orange-600" selected={selectedMode === 'coop'} onClick={() => setSelectedMode('coop')} />
                  <TravelModeCard id="pvp" title="高性价比" desc="精打细算，利用算法匹配最优的体验成本比。" icon={<PiggyBank className="w-6 h-6" />} color="text-rose-600" selected={selectedMode === 'pvp'} onClick={() => setSelectedMode('pvp')} />
                </div>

                {/* 我的当前诉求设定卡片 */}
                <div className="bg-white/80 backdrop-blur-md rounded-2xl p-6 shadow-sm border border-slate-100 flex items-center justify-between">
                  <div className="flex items-start gap-4">
                    <div className="p-3 bg-orange-50 rounded-xl shrink-0 text-orange-500">
                      <Sparkles className="w-6 h-6" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-base font-extrabold text-slate-800">我的旅程诉求画像</h3>
                        <span className="text-xs bg-orange-100 text-orange-700 px-2 py-0.5 rounded-md font-bold">{selectedRole}</span>
                      </div>
                      <p className="text-xs text-slate-500 leading-relaxed font-medium">{myIntent}</p>
                    </div>
                  </div>
                  <button onClick={() => setShowEditIntentModal(true)} className="px-3.5 py-2 bg-slate-50 hover:bg-orange-50 text-slate-600 hover:text-orange-600 rounded-xl text-xs font-bold border border-slate-200 transition-colors flex items-center gap-1.5 cursor-pointer">
                    <Edit3 className="w-3.5 h-3.5" /> 修改诉求
                  </button>
                </div>

                <div className="bg-white/80 backdrop-blur-md rounded-2xl p-6 shadow-sm border border-slate-100 flex items-start gap-4">
                  <div className="p-3 bg-orange-50 rounded-xl shrink-0">
                    <BrainCircuit className="w-6 h-6 text-orange-500" />
                  </div>
                  <div>
                    <h3 className="text-base font-extrabold text-slate-800 mb-1.5">
                      {selectedMode === 'solo' && "✨ 专注自我，深度探索"}
                      {selectedMode === 'coop' && '✨ 什么是“基于房间的多智能体协同”？'}
                      {selectedMode === 'pvp' && "✨ 极致性价比是如何做到的？"}
                    </h3>
                    <p className="text-xs text-slate-600 leading-relaxed font-medium">
                      {selectedMode === 'solo' && "系统将关闭社交与分享冗余功能，为您启动单人专属适应度函数。"}
                      {selectedMode === 'coop' && `同一房间码 [${roomCode}] 内的所有真实在线成员，会各自被派驻专属智能体，在共享推演黑板上进行帕累托博弈，兼顾寻味、拍照、休闲等不同成员诉求。`}
                      {selectedMode === 'pvp' && "底层的精算智能体会根据距离、评价、交通成本构建高维拓扑图，像极客一样为您裁剪掉每一笔不必要的开销。"}
                    </p>
                  </div>
                </div>
              </div>

              <div className="lg:col-span-4 flex flex-col h-full bg-white/60 backdrop-blur-md rounded-3xl shadow-sm border border-slate-200 overflow-hidden relative">
                <AnimatePresence mode="wait">
                  {renderRightPanel()}
                </AnimatePresence>
              </div>
            </main>

            {/* ============ 主页底部板块：灵感目的地 / 社区入口 / 进化日志 ============ */}
            <div className="px-8 sm:px-10 pb-4 grid grid-cols-1 md:grid-cols-3 gap-4">
              <div onClick={() => setShowDestinationMap(true)} className="group bg-white/60 backdrop-blur-md border border-slate-200/70 rounded-2xl p-5 cursor-pointer hover:border-emerald-300 hover:shadow-lg hover:shadow-emerald-100/50 transition-all">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Compass className="w-4 h-4 text-emerald-600" />
                    <h3 className="text-sm font-extrabold text-slate-800">灵感目的地图谱</h3>
                  </div>
                  <span className="text-[10px] font-black text-emerald-600 bg-emerald-50 border border-emerald-100 px-2 py-0.5 rounded-full">{Object.keys(PROVINCE_DATA).length} 目的地</span>
                </div>
                <p className="text-[11px] text-slate-500 font-medium leading-relaxed mb-3">交互式全国目的地地图：搜索筛选、点击标记看景点详情、一键开启智能规划。</p>
                <div className="flex flex-wrap gap-1.5">
                  {TOP_DESTINATIONS.map(c => (
                    <span key={c} className="text-[11px] bg-emerald-50 text-emerald-700 px-2.5 py-1 rounded-full font-bold group-hover:bg-emerald-100 transition-colors">{shortenRegionName(c)}</span>
                  ))}
                </div>
                <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 font-bold mt-3">打开交互式地图 <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" /></span>
              </div>

              <div onClick={() => setShowCommunity(true)} className="bg-white/60 backdrop-blur-md border border-slate-200/70 rounded-2xl p-5 cursor-pointer hover:border-orange-300 hover:shadow-md transition-all">
                <div className="flex items-center gap-2 mb-3">
                  <MessageCircle className="w-4 h-4 text-orange-500" />
                  <h3 className="text-sm font-extrabold text-slate-800">同行者社区</h3>
                </div>
                <p className="text-xs text-slate-500 leading-relaxed font-medium">分享满意行程，点赞、评论、回复，让高质量路书被更多旅行者与智能体借鉴。</p>
                <span className="inline-flex items-center gap-1 text-[11px] text-orange-600 font-bold mt-3">进入社区 <ArrowRight className="w-3 h-3" /></span>
              </div>

              <div className="bg-white/60 backdrop-blur-md border border-slate-200/70 rounded-2xl p-5">
                <div className="flex items-center gap-2 mb-3">
                  <BrainCircuit className="w-4 h-4 text-blue-500" />
                  <h3 className="text-sm font-extrabold text-slate-800">智能体进化日志</h3>
                </div>
                {[
                  '融合全网评价与真实反馈，路线持续自我修正',
                  '社区高质量行程作为软引导，辅助共识推演',
                  '帕累托博弈在每一轮协商中自适应校准'
                ].map((t, i) => (
                  <div key={i} className="flex items-start gap-2 mb-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 mt-1.5 shrink-0"></span>
                    <p className="text-[11px] text-slate-500 font-medium leading-relaxed">{t}</p>
                  </div>
                ))}
              </div>
            </div>

            <footer className="p-6 sm:p-8 border-t border-slate-200/50 bg-white/30 flex justify-between items-center">
              <span className="text-xs text-slate-400 font-bold hidden sm:inline">
                当前协同房间：<strong className="text-slate-700 font-mono">{roomCode}</strong>（将此房间码分享给同行好友即可同屏规划）
              </span>
              <button onClick={handleBeginPlan} className="ml-auto px-8 py-3.5 bg-orange-500 text-white rounded-2xl font-bold text-sm flex items-center justify-center gap-3 hover:bg-orange-600 shadow-lg shadow-orange-200/50 hover:-translate-y-0.5 transition-all duration-300 cursor-pointer">
                下一步：定制主控体验 <ArrowRight className="w-4 h-4" />
              </button>
            </footer>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ================= 修改个人诉求画像 Modal ================= */}
      <AnimatePresence>
        {showEditIntentModal && (
          <EditIntentModal 
            currentRole={selectedRole}
            currentIntent={myIntent}
            onClose={() => setShowEditIntentModal(false)}
            onSave={handleUpdateMyIntent}
          />
        )}
      </AnimatePresence>

      {/* ================= 主控诉求定制弹窗 ================= */}
      <AnimatePresence>
        {showPreferenceModal && !isTransitioning && (
          <motion.div key="auth-modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-slate-900/20 backdrop-blur-sm z-[100] flex items-center justify-center p-4">
            <motion.div key="auth-modal-dialog" initial={{ scale: 0.95, y: 20 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: -20 }} className="bg-white rounded-3xl w-full max-w-xl p-8 sm:p-10 shadow-2xl border border-slate-100">
              <div className="text-center mb-6">
                <h3 className="text-2xl font-extrabold text-slate-800 mb-2">这趟旅程，你最期待什么？</h3>
                <p className="text-xs sm:text-sm text-slate-500 font-medium">定制您的主控角色画像，智能体矩阵将根据全员矩阵进行博弈均衡。</p>
              </div>
              <div className="grid grid-cols-2 gap-3 mb-8">
                <PreferenceCard id="foodie" icon={<Coffee/>} title="寻味探索" desc="美食驱动，为您匹配地道餐馆。" selected={selectedRole === '寻味探索'} onClick={() => setSelectedRole('寻味探索')} color="border-orange-500 bg-orange-50/80 text-orange-600" />
                <PreferenceCard id="photo" icon={<Camera/>} title="视觉体验" desc="出片导向，精准匹配最佳摄影光线。" selected={selectedRole === '视觉体验'} onClick={() => setSelectedRole('视觉体验')} color="border-purple-500 bg-purple-50/80 text-purple-600" />
                <PreferenceCard id="chill" icon={<Car/>} title="休闲漫步" desc="拒绝特种兵，安排宽裕休息漫游。" selected={selectedRole === '休闲漫步'} onClick={() => setSelectedRole('休闲漫步')} color="border-blue-500 bg-blue-50/80 text-blue-600" />
                <PreferenceCard id="hardcore" icon={<MapIcon/>} title="深度探索" desc="行程紧凑，打卡最多核心文旅地标。" selected={selectedRole === '深度探索'} onClick={() => setSelectedRole('深度探索')} color="border-emerald-500 bg-emerald-50/80 text-emerald-600" />
              </div>
              <div className="flex gap-3">
                <button onClick={() => !isConnecting && setShowPreferenceModal(false)} className="flex-1 py-3 bg-slate-50 border border-slate-200 text-slate-600 font-bold rounded-xl hover:bg-slate-100 transition-colors cursor-pointer text-sm">返回调整</button>
                <button onClick={handleConfirmSync} disabled={!selectedRole || isConnecting} className={`flex-1 py-3 rounded-xl font-bold flex items-center justify-center gap-2 transition-all cursor-pointer text-sm ${!selectedRole ? 'bg-slate-100 text-slate-400' : isConnecting ? 'bg-orange-500 text-white' : 'bg-orange-500 text-white hover:bg-orange-600 shadow-md shadow-orange-200/50'}`}>
                  {isConnecting ? <><RefreshCw className="w-4 h-4 animate-spin"/>激活多智能体...</> : <>进入共识沙盘 <ArrowRight className="w-4 h-4" /></>}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ================= 灵感目的地图谱 Modal ================= */}
      <AnimatePresence>
        {showDestinationMap && (
          <DestinationMap
            onClose={() => setShowDestinationMap(false)}
            onStartPlan={handleStartPlanFromDestination}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

// ================= 修改个人诉求画像 Modal =================
function EditIntentModal({ currentRole, currentIntent, onClose, onSave }: any) {
  const [role, setRole] = useState(currentRole);
  const [intent, setIntent] = useState(currentIntent);
  const roles = ['寻味探索', '视觉体验', '休闲漫步', '深度探索'];

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-[110] flex items-center justify-center p-4">
      <motion.div initial={{ scale: 0.95, y: 15 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: -15 }} className="bg-white rounded-3xl w-full max-w-md p-7 shadow-2xl border border-slate-100 relative">
        <button onClick={onClose} className="absolute top-5 right-5 p-1.5 rounded-full hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors cursor-pointer">
          <X className="w-5 h-5" />
        </button>
        <h3 className="text-lg font-black text-slate-800 mb-1">修改我的旅行画像</h3>
        <p className="text-xs text-slate-400 mb-4">修改后将实时同步至同一房间内所有好友的沙盘中</p>

        <div className="mb-4">
          <label className="text-xs font-bold text-slate-600 mb-1.5 block">偏好定位</label>
          <div className="grid grid-cols-2 gap-2">
            {roles.map(r => (
              <button key={r} onClick={() => setRole(r)} className={`py-2 px-3 text-xs font-bold rounded-xl border transition-all cursor-pointer ${role === r ? 'bg-orange-50 border-orange-500 text-orange-600 shadow-2xs' : 'bg-slate-50 border-slate-200 text-slate-600'}`}>{r}</button>
            ))}
          </div>
        </div>

        <div className="mb-5">
          <label className="text-xs font-bold text-slate-600 mb-1 block">具体出行诉求</label>
          <textarea rows={3} value={intent} onChange={(e) => setIntent(e.target.value)} className="w-full p-3 bg-slate-50 border border-slate-200 rounded-xl text-xs outline-none focus:border-orange-500 focus:bg-white resize-none font-medium leading-relaxed" />
        </div>

        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 py-2.5 bg-slate-100 text-slate-600 font-bold rounded-xl text-xs cursor-pointer">取消</button>
          <button onClick={() => onSave(role, intent)} className="flex-1 py-2.5 bg-orange-500 text-white font-bold rounded-xl text-xs shadow-md shadow-orange-200 cursor-pointer">保存并广播同步</button>
        </div>
      </motion.div>
    </motion.div>
  );
}

// ================= 工作区区域 =================
function UnifiedWorkspace({ mode, role, roomCode, roomMembers, currentUser, initialDestination, onBack }: { mode: string, role: string, roomCode: string, roomMembers: RoomMember[], currentUser: UserProfile, initialDestination?: string | null, onBack: () => void }) {
  const [phase, setPhase] = useState<Phase>('drafting');
  const [intentsReady, setIntentsReady] = useState(false);
  const [showBlackboard, setShowBlackboard] = useState(false);
  const [tripSaved, setTripSaved] = useState(false);
  
  const [selectedPoiIndex, setSelectedPoiIndex] = useState<number | null>(0);
  const [activeDayIndex, setActiveDayIndex] = useState<number>(0);

  const [userIntent, setUserIntent] = useState(initialDestination ? `去${initialDestination}玩3天` : '');
  const [dynamicRoutes, setDynamicRoutes] = useState<any[]>([]);
  const [realPath, setRealPath] = useState<[number, number][]>([]);
  const [targetCityInfo, setTargetCityInfo] = useState<{name: string, lnglat: [number, number]} | null>(null);

  const [weatherInfo, setWeatherInfo] = useState<any>(null);
  const [trafficInfo, setTrafficInfo] = useState<any>(null);
  const [safetyInfo, setSafetyInfo] = useState<any>(null);
  const [travelDetails, setTravelDetails] = useState<Record<string, any>>({});
  
  const [budgetData, setBudgetData] = useState<any>(null);

  // 动态团队满意度矩阵
  const [teamSatisfaction, setTeamSatisfaction] = useState<Record<string, number>>({});
  const [arbitrationRecords, setArbitrationRecords] = useState<string[]>([
    "针对【餐饮与预算诉求】: 精选特色老字号正餐，全程无重复排布",
    "针对【节奏分歧】: 午后采取分合流调度，傍晚在统一地点汇合用餐"
  ]);

  const [streamedText, setStreamedText] = useState(""); 
  const [consensusSummary, setConsensusSummary] = useState('');
  const [apiError, setApiError] = useState<string | null>(null);

  const [pvpPriceIntel, setPvpPriceIntel] = useState<string>("");
  const [pvpPriceLoading, setPvpPriceLoading] = useState<boolean>(true);

  const [incrementalHistory, setIncrementalHistory] = useState<string[]>([]);
  const [nodeVotes, setNodeVotes] = useState<Record<string, 'up' | 'down'>>({});

  const wsRef = useRef<WebSocket | null>(null);
  const autoSavedRef = useRef(false);

  // 初始化满意度分数
  useEffect(() => {
    const initialSat: Record<string, number> = {};
    roomMembers.forEach((m, idx) => {
      initialSat[m.name] = 93 + (idx % 4);
    });
    setTeamSatisfaction(initialSat);
  }, [roomMembers]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    const connect = () => {
      if (!isMounted) return;
      ws = new WebSocket(`${WS_BASE}/ws?room_id=${encodeURIComponent(roomCode || 'OMNI-88')}&user_id=${encodeURIComponent(currentUser.id)}`);
      
      ws.onopen = () => {
        setApiError(null); 
      };
      
      ws.onmessage = (event) => {
        try {
          const rawData = event.data as string;
          const lines = rawData.split('\n').filter((line: string) => line.trim().length > 0);
          
          for (const line of lines) {
            let msg: any;
            try { msg = JSON.parse(line); } catch { continue; }
          
            if (msg.type === "target_city") {
              setTargetCityInfo(msg.payload);
            } else if (msg.type === "weather_info") {
              setWeatherInfo(msg.payload);
            } else if (msg.type === "traffic_info") {
              setTrafficInfo(msg.payload);
            } else if (msg.type === "safety_info") {
              setSafetyInfo(msg.payload);
            } else if (msg.type === "travel_details") {
              setTravelDetails(msg.payload);
            } else if (msg.type === "budget_breakdown") {
              setBudgetData(msg.payload);
            } else if (msg.type === "stream_token") {
              setStreamedText(prev => {
                const rawToken = msg.payload ? String(msg.payload) : "";
                const cleanToken = rawToken.replace(/null/g, "");
                const cleanPrev = (prev || "").replace(/null/g, "");
                const newText = cleanPrev + cleanToken;
                
                const finalData = tryExtractJson(newText);
                if (finalData && finalData.route && Array.isArray(finalData.route) && finalData.route.length > 0) {
                  const mappedRoutes = finalData.route.map((r: any) => ({
                    day: r.day || 1,
                    name: r.location || r.name,
                    lnglat: normalizeLnglat(r.lnglat) || [87.61, 43.82],
                    color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6", 
                    desc: r.desc || r.action,
                    time: r.time,             
                    time_reason: r.time_reason || "",
                    transport: r.transport,   
                    tags: r.tags || [],             
                    cost: r.cost_estimate || r.cost || "¥45/人",
                    photos: r.photos || [],
                    trust_reason: r.trust_reason || "核心地标推荐",
                    amap_url: r.amap_url || "",
                    map_image: r.map_image || "",
                    rating: r.rating || "4.6",
                    open_time: r.open_time || "全天开放",
                    address: r.address || "",
                    hotel_candidates: r.hotel_candidates || [],
                    split_info: r.split_info || "",
                    merge_point: Boolean(r.merge_point),
                    is_hotel: Boolean(r.is_hotel || r.tags?.includes("住宿"))
                  }));
                  
                  setDynamicRoutes(mappedRoutes);
                  if (finalData.negotiation_summary) setConsensusSummary(finalData.negotiation_summary);
                  if (finalData.team_satisfaction) setTeamSatisfaction(finalData.team_satisfaction);
                  if (finalData.arbitration_records) setArbitrationRecords(finalData.arbitration_records);
                  setSelectedPoiIndex(0);
                  setActiveDayIndex(0);
                  setPhase('decision');
                } else {
                  const incrementalItems = extractStreamingRoutes(newText);
                  if (incrementalItems.length > 0) {
                    setDynamicRoutes(incrementalItems);
                    setPhase('decision');
                  }
                }
                return newText;
              });
            } else if (msg.type === "error") {
              setApiError(msg.payload);
              setIntentsReady(false);
              setPhase('drafting');
              setShowBlackboard(false);
            } else if (msg.type === "pvp_price") {
              setPvpPriceIntel(msg.payload);
              setPvpPriceLoading(false);
            } else if (msg.type === "actual_path") {
              setRealPath(Array.isArray(msg.payload) ? msg.payload : []);
            } else if (msg.type === "final_route") {
              const fd = msg.payload || {};
              const fr = Array.isArray(fd.route) ? fd.route : [];
              if (fr.length > 0) {
                const mapped = fr.map((r: any) => ({
                  day: r.day || 1,
                  name: r.location || r.name,
                  lnglat: normalizeLnglat(r.lnglat) || [87.61, 43.82],
                  color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6",
                  desc: r.desc || r.action,
                  time: r.time,
                  time_reason: r.time_reason || "",
                  transport: r.transport,
                  tags: r.tags || [],
                  cost: r.cost_estimate || r.cost || "¥45/人",
                  photos: r.photos || [],
                  trust_reason: r.trust_reason || "核心地标推荐",
                  amap_url: r.amap_url || "",
                  map_image: r.map_image || "",
                  rating: r.rating || "4.6",
                  open_time: r.open_time || "全天开放",
                  address: r.address || "",
                  hotel_candidates: r.hotel_candidates || [],
                  split_info: r.split_info || "",
                  merge_point: Boolean(r.merge_point),
                  is_hotel: Boolean(r.is_hotel || r.tags?.includes("住宿"))
                }));
                setDynamicRoutes(mapped);
                setSelectedPoiIndex(0);
                setActiveDayIndex(0);
                setPhase('decision');
              }
              if (fd.negotiation_summary) setConsensusSummary(fd.negotiation_summary);
              if (fd.team_satisfaction) setTeamSatisfaction(fd.team_satisfaction);
              if (fd.arbitration_records) setArbitrationRecords(fd.arbitration_records);
            }
          }
        } catch (err) {}
      };

      ws.onerror = () => setApiError("协同网络中断，请检查后端网关是否正常运行。");
      ws.onclose = () => {
        if (!isMounted) return;
        reconnectTimer = setTimeout(connect, 2000);
      };
      wsRef.current = ws;
    };

    connect();

    return () => {
      isMounted = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [roomCode, currentUser.id]);

  const humanReadableLogs = useMemo(() => {
    if (!streamedText) return "多智能体空间博弈推演中...";
    
    let thoughtPart = streamedText;
    if (thoughtPart.includes("[FINAL_JSON]")) {
      thoughtPart = thoughtPart.split("[FINAL_JSON]")[0];
    } else if (thoughtPart.includes("```json")) {
      thoughtPart = thoughtPart.split("```json")[0];
    } else {
      const firstJsonIdx = thoughtPart.search(/\{\s*"status"|\{\s*"route"/);
      if (firstJsonIdx !== -1) {
        thoughtPart = thoughtPart.substring(0, firstJsonIdx);
      }
    }

    const cleaned = thoughtPart.replace(/null/g, "").trim();
    return cleaned || "[系统中枢]: 多智能体正在分析全员偏好矩阵与分合流图论，生成定制路书中...";
  }, [streamedText]);

  const totalDays = useMemo(() => {
    if (!dynamicRoutes || dynamicRoutes.length === 0) return [1];
    const daysSet = new Set<number>(dynamicRoutes.map((r: any) => Number(r.day) || 1));
    return Array.from(daysSet).sort((a, b) => a - b);
  }, [dynamicRoutes]);

  const currentDayRoutes = useMemo(() => {
    if (!dynamicRoutes || dynamicRoutes.length === 0) return [];
    const targetDay = totalDays[activeDayIndex] || 1;
    return dynamicRoutes.filter((r: any) => (Number(r.day) || 1) === targetDay);
  }, [dynamicRoutes, activeDayIndex, totalDays]);

  const handleSubmitIntent = () => {
    if (!userIntent.trim() || !wsRef.current) return;

    setIntentsReady(true);
    setPhase('deduction');
    setShowBlackboard(true); 
    setApiError(null);
    setStreamedText(""); 

    const updatedHistory = [...incrementalHistory, userIntent];
    setIncrementalHistory(updatedHistory);

    // 动态同步发送当前房间全部成员画像
    const wsPayload = {
      type: "agent_negotiate",
      payload: {
        destinations: [],
        user_preferences: {
          mode: mode,
          role: role, 
          intent: userIntent,
          history_sequence: updatedHistory,
          current_existing_route: dynamicRoutes,
          room_members: roomMembers.map(m => ({
            id: m.id,
            name: m.name,
            role: m.role,
            intent: m.intent
          }))
        }
      }
    };
    try {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(wsPayload));
      } else {
        setApiError("WebSocket 连接已断开，请刷新页面后重试。");
        setIntentsReady(false);
        setPhase('drafting');
        setShowBlackboard(false);
      }
    } catch (e) {
      setApiError("消息发送失败，请刷新页面后重试。");
      setIntentsReady(false);
      setPhase('drafting');
      setShowBlackboard(false);
    }
  };

  const handleResetIntent = () => {
    setPhase('drafting');
    setIntentsReady(false);
    setSelectedPoiIndex(null);
  };

  const handleSaveTrip = async () => {
    if (!dynamicRoutes || dynamicRoutes.length === 0) return;
    const cityName = targetCityInfo?.name || '';
    const title = `${cityName || '我的行程'}${totalDays.length}日行程`;
    const content = JSON.stringify({
      summary: consensusSummary,
      city: cityName,
      days: totalDays,
      routes: dynamicRoutes
    });
    try {
      const res = await fetch(`${API_BASE}/api/user/trip`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, title, dest_city: cityName, content })
      });
      const rawText = await res.text();
      let data: any = null;
      try { data = JSON.parse(rawText); } catch { throw new Error(`行程响应异常 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '行程保存失败');
      setTripSaved(true);
      setTimeout(() => setTripSaved(false), 1800);
    } catch (e: any) {
      setApiError(e.message || '行程保存失败');
    }
  };

  // 👑 自动保存到“我的路书”：首次生成完整路线后静默落库，无需手动点击
  useEffect(() => {
    if (phase === 'decision' && dynamicRoutes.length > 0 && !autoSavedRef.current) {
      autoSavedRef.current = true;
      handleSaveTrip();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, dynamicRoutes]);

  const handleSinglePoiSwap = (poiName: string) => {
    const currentCityName = targetCityInfo?.name || '乌鲁木齐';
    const swapPrompt = `保持在【${currentCityName}】不变，仅把行程中的景点【${poiName}】平替换成【${currentCityName}】另一个同等知名度的高评分文旅景点或特色餐厅，绝对不要改变城市和其他天数的规划。`;
    setUserIntent(swapPrompt);
    handleSubmitIntent();
  };

  const handleVote = (poiName: string, type: 'up' | 'down') => {
    const nextValue = nodeVotes[poiName] === type ? null : type;
    setNodeVotes(prev => ({
      ...prev,
      [poiName]: nextValue as any
    }));
    // 上报正/负反馈到进化闭环，形成系统记忆
    fetch(`${API_BASE}/api/v1/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_id: roomCode, user_id: currentUser.id, target: poiName, score: type === 'up' ? 1 : -1, reason: '用户对路线节点评价' })
    }).catch(() => {});
    if (type === 'down') {
      handleSinglePoiSwap(poiName);
    }
  };

  // 👑 A/B 方案：换一个调性，生成与当前版本不同的 B 方案
  const handleRegenerateVariant = () => {
    const currentCityName = targetCityInfo?.name || '乌鲁木齐';
    const variantPrompt = `请给【${currentCityName}】规划一个与当前方案不同调性的 B 版本：换一种行程节奏与景点组合（例如更松弛 vs 更紧凑、或更偏爱小众 vs 更主打地标打卡），保持城市与天数不变，重新推演一版全新路线。`;
    setUserIntent(variantPrompt);
    handleSubmitIntent();
  };

  return (
    <motion.div key="workspace-root" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} className="h-screen w-full bg-white flex overflow-hidden font-sans text-slate-800">
      
      {/* 左侧长图文游记与路径排版区 */}
      <div className="w-[620px] xl:w-[700px] flex flex-col relative z-20 border-r border-slate-200 bg-white shadow-2xl shrink-0">
        <header className="px-8 py-5 border-b border-slate-100 flex justify-between items-center bg-white shadow-xs z-10">
          <div>
            <h2 className="text-2xl font-black text-slate-800 tracking-tight">{targetCityInfo ? targetCityInfo.name + '全景路线规划' : '规划您的行程'}</h2>
            <p className="text-xs text-slate-400 font-bold mt-0.5">OmniRoute 多智能体联网知识增强交付</p>
          </div>
          {phase === 'decision' && (
            <div className="flex items-center gap-2">
              <button onClick={handleSaveTrip} className="px-3.5 py-1.5 bg-emerald-50 text-emerald-600 rounded-xl font-bold text-xs hover:bg-emerald-100 transition-colors flex items-center gap-1.5 border border-emerald-100 shadow-2xs cursor-pointer">
                {tripSaved ? <Check className="w-3.5 h-3.5" /> : <Save className="w-3.5 h-3.5" />} {tripSaved ? '已保存' : '保存到我的行程'}
              </button>
              <button onClick={handleRegenerateVariant} className="px-3.5 py-1.5 bg-blue-50 text-blue-600 rounded-xl font-bold text-xs hover:bg-blue-100 transition-colors flex items-center gap-1.5 border border-blue-100 shadow-2xs cursor-pointer">
                <Shuffle className="w-3.5 h-3.5"/> 换一版
              </button>
              <button onClick={() => { setPhase('drafting'); setIntentsReady(false); }} className="px-3.5 py-1.5 bg-orange-50 text-orange-600 rounded-xl font-bold text-xs hover:bg-orange-100 transition-colors flex items-center gap-1.5 border border-orange-100 shadow-2xs cursor-pointer">
                <Wand2 className="w-3.5 h-3.5"/> 重新调整
              </button>
            </div>
          )}
        </header>

        <div className="flex-1 overflow-y-auto bg-white custom-scrollbar relative">
          <AnimatePresence mode="wait">
            {phase === 'drafting' && (
              <DraftingPanel 
                key="workspace-drafting-panel"
                mode={mode} 
                onSubmit={handleSubmitIntent} 
                isReady={intentsReady} 
                userIntent={userIntent} 
                setUserIntent={setUserIntent} 
                apiError={apiError} 
                historyLength={incrementalHistory.length}
                roomMembers={roomMembers}
              />
            )}
            {phase === 'deduction' && (
              <DeductionPanel 
                key="workspace-deduction-panel" 
                latestLog={humanReadableLogs.split('\n').filter(Boolean).pop()}
              />
            )}
            {phase === 'decision' && (
              <motion.div key="workspace-decision-panel" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <MafengwoStylePanel 
                  routes={currentDayRoutes} 
                  totalDays={totalDays}
                  activeDayIndex={activeDayIndex}
                  onSelectDay={setActiveDayIndex}
                  travelDetails={travelDetails} 
                  selectedPoiIndex={selectedPoiIndex} 
                  onSelectPoi={setSelectedPoiIndex} 
                  weatherInfo={weatherInfo}
                  trafficInfo={trafficInfo}
                  onReset={handleResetIntent}
                  consensusSummary={consensusSummary}
                  budgetData={budgetData}
                  onSwapPoi={handleSinglePoiSwap}
                  roomCode={roomCode}
                  roomMembers={roomMembers}
                  teamSatisfaction={teamSatisfaction}
                  arbitrationRecords={arbitrationRecords}
                  nodeVotes={nodeVotes}
                  onVote={handleVote}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* 右侧：地图与全视图渲染 */}
      <div className="relative flex-1 bg-slate-100 overflow-hidden flex flex-col">
        <FullRouteVisualizer 
          phase={phase}
          routes={currentDayRoutes}
          actualPath={realPath}
          selectedPoiIndex={selectedPoiIndex}
          onPoiSelect={setSelectedPoiIndex}
          onWakeAgent={() => setShowBlackboard(true)}
          onExit={onBack}
          targetCityInfo={targetCityInfo}
          travelDetails={travelDetails}
          safetyInfo={safetyInfo}
          weatherInfo={weatherInfo}
          trafficInfo={trafficInfo}
          budgetData={budgetData}
        />

        <div className="absolute top-6 right-6 z-40 flex items-center gap-3 pointer-events-auto">
          <button 
            onClick={() => setShowBlackboard(!showBlackboard)} 
            className="bg-white/90 backdrop-blur-md p-2.5 rounded-xl shadow-lg text-slate-700 hover:text-orange-500 border border-slate-200/80 transition-all hover:scale-105 cursor-pointer flex items-center gap-1.5 text-xs font-bold"
            title="查看多智能体博弈黑板"
          >
            <BrainCircuit className="w-4 h-4 text-orange-500"/>
            <span className="hidden sm:inline">博弈黑板</span>
          </button>
        </div>

        <div className="absolute bottom-6 left-0 w-full z-30 pointer-events-none">
          <AnimatePresence>
            {phase === 'decision' && (
              <motion.div key="bottom-carousel-wrapper" initial={{ y: 120, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 120, opacity: 0 }} className="pointer-events-auto flex flex-col items-center">
                <BottomMapCarousel selectedIndex={selectedPoiIndex} onSelect={setSelectedPoiIndex} routes={currentDayRoutes} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
        
        <AnimatePresence>
          {showBlackboard && (
            <motion.div key="blackboard-drawer-panel" initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }} transition={{ type: "spring", damping: 25 }} className="absolute top-0 right-0 w-[420px] h-full bg-white/95 backdrop-blur-2xl shadow-2xl z-50 flex flex-col border-l border-slate-200">
              <div className="p-5 border-b border-slate-100 flex justify-between items-center bg-slate-50">
                <h3 className="font-bold text-slate-800 flex items-center gap-2 text-sm"><BrainCircuit className="w-4 h-4 text-orange-500"/> 多智能体推演日志 (Blackboard)</h3>
                <button onClick={() => setShowBlackboard(false)} className="p-1 hover:bg-slate-200 rounded-md text-slate-400 cursor-pointer"><X className="w-4 h-4"/></button>
              </div>
              <div className="p-6 overflow-y-auto text-xs font-mono text-slate-600 whitespace-pre-wrap custom-scrollbar leading-relaxed">
                {humanReadableLogs}
                {phase === 'deduction' && <span className="inline-block w-2 h-4 bg-orange-500 animate-pulse ml-1 align-middle"></span>}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

      </div>
    </motion.div>
  );
}

// -------------------------------------------------------------------------
// 大厅与工具子组件
// -------------------------------------------------------------------------
function TravelModeCard({ title, desc, icon, color, selected, onClick }: any) {
  return (
    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} onClick={onClick} className={`cursor-pointer p-5 sm:p-6 rounded-2xl border transition-all duration-300 flex flex-col group ${selected ? 'border-orange-500 bg-orange-50/60 shadow-md' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
      <div className={`mb-4 w-11 h-11 rounded-xl flex items-center justify-center transition-transform ${selected ? 'bg-orange-500 text-white shadow-sm' : 'bg-slate-100 text-slate-500 group-hover:scale-110'}`}>
        <div className={selected ? 'text-white' : color}>{icon}</div>
      </div>
      <div>
        <h3 className="text-base sm:text-lg font-extrabold text-slate-800 mb-1">{title}</h3>
        <p className="text-xs text-slate-500 font-medium leading-relaxed">{desc}</p>
      </div>
    </motion.div>
  );
}

function PreferenceCard({ icon, title, desc, selected, onClick, color }: any) {
  return (
    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }} onClick={onClick} className={`cursor-pointer p-4 rounded-xl border-2 transition-all relative ${selected ? color : 'border-slate-100 bg-white'}`}>
      {selected && <div className="absolute top-3 right-3 text-orange-500"><Check className="w-4 h-4" /></div>}
      <div className={`mb-2 ${selected ? '' : 'text-slate-400'}`}>{React.cloneElement(icon, { className: 'w-5 h-5' })}</div>
      <h4 className={`text-sm font-extrabold mb-1 ${selected ? 'text-slate-900' : 'text-slate-700'}`}>{title}</h4>
      <p className={`text-[11px] font-medium leading-snug ${selected ? 'text-slate-700' : 'text-slate-500'}`}>{desc}</p>
    </motion.div>
  );
}

function DraftingPanel({ mode, onSubmit, isReady, userIntent, setUserIntent, apiError, historyLength, roomMembers }: any) {
  const [listening, setListening] = useState(false);
  const recRef = useRef<any>(null);

  const toggleVoice = () => {
    if (listening) {
      recRef.current?.stop();
      setListening(false);
      return;
    }
    const w = (window as any);
    const SR = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!SR) {
      alert('当前浏览器不支持语音输入，请使用 Chrome / Edge');
      return;
    }
    const rec = new SR();
    rec.lang = 'zh-CN';
    rec.continuous = false;
    rec.interimResults = false;
    rec.onresult = (e: any) => {
      const text = e.results?.[0]?.[0]?.transcript;
      if (text) setUserIntent((prev: string) => (prev ? prev + '，' + text : text));
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => setListening(false);
    recRef.current = rec;
    setListening(true);
    rec.start();
  };

  return (
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="p-8 flex flex-col h-full relative">
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-orange-50/90 to-amber-50/60 border border-orange-100 p-5 shadow-xs mb-6">
        <h3 className="text-sm font-extrabold text-orange-900 mb-2 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-orange-500" />
          {historyLength > 0 ? `多轮对齐模式 (已追加 ${historyLength} 次诉求)` : `专属向导已就绪 (当前共识组: ${roomMembers.length}人)`}
        </h3>
        <p className="text-xs text-orange-700/80 leading-relaxed font-medium">
          {historyLength > 0 ? '支持增量精进！您可以直接输入：“把第一天的路线缩短”、“中午想吃抓饭”等。系统会在当前成果上打差量补丁。' : '随性写下旅程目标（城市、天数、特殊愿望），底层的多个智能体会同时协调所有同行人的偏好与预算。'}
        </p>
      </div>

      {apiError && (
        <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} className="mb-4 p-4 bg-rose-50 border border-rose-200 rounded-2xl flex items-start gap-3 shadow-sm">
          <AlertCircle className="w-5 h-5 text-rose-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-rose-700">引擎连接失败</p>
            <p className="text-xs text-rose-500 mt-1 leading-relaxed">{apiError}</p>
          </div>
        </motion.div>
      )}

      <div className="flex-1 flex flex-col mb-8">
        <div className={`flex-1 min-h-[220px] p-5 bg-white rounded-2xl shadow-[0_4px_20px_rgba(0,0,0,0.03)] border transition-all duration-300 flex flex-col group relative ${isReady ? 'border-emerald-200 bg-emerald-50/30' : 'border-slate-200 focus-within:border-orange-400 focus-within:shadow-xl focus-within:ring-4 focus-within:ring-orange-50'}`}>
          <div className="flex justify-between items-center mb-3">
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
              <MessageSquare className="w-3.5 h-3.5" /> {historyLength > 0 ? "追问/精进当前路线需求" : "你的个性化旅行诉求"}
            </span>
            <div className="flex items-center gap-2">
              <button onClick={toggleVoice} disabled={isReady} title="语音输入" className={`p-1.5 rounded-lg border transition-colors cursor-pointer ${listening ? 'bg-rose-50 border-rose-200 text-rose-500' : 'bg-slate-50 border-slate-200 text-slate-500 hover:text-orange-500 hover:border-orange-200'} ${isReady ? 'opacity-50 cursor-not-allowed' : ''}`}>
                <Mic className={`w-3.5 h-3.5 ${listening ? 'animate-pulse' : ''}`} />
              </button>
              {isReady && <span className="text-[10px] bg-emerald-100 text-emerald-600 px-2.5 py-1 rounded-full font-bold flex items-center gap-1"><Check className="w-3 h-3"/> 已提交推演</span>}
            </div>
          </div>
          
          <textarea 
            disabled={isReady}
            value={userIntent}
            onChange={(e) => setUserIntent(e.target.value)}
            className="w-full flex-1 bg-transparent resize-none outline-none text-sm text-slate-700 placeholder:text-slate-300 font-medium leading-relaxed custom-scrollbar"
            placeholder={historyLength > 0 ? "例如：还是去那儿，但是把第二天的午餐平替成便宜一点的老字号小吃..." : "例如：我们打算去乌鲁木齐玩3天，想看大巴扎和博物馆，吃地道手抓肉，下午不能太累..."}
          ></textarea>
        </div>
      </div>

      <div className="mt-auto pt-4">
        <button 
          onClick={onSubmit} 
          disabled={isReady || userIntent.trim().length === 0}
          className={`w-full py-4 rounded-2xl font-bold text-sm flex items-center justify-center gap-2 transition-all duration-300 shadow-lg cursor-pointer ${isReady ? 'bg-emerald-500 text-white shadow-emerald-200' : userIntent.trim() ? 'bg-orange-500 text-white hover:bg-orange-600 shadow-orange-200/60 hover:-translate-y-0.5' : 'bg-slate-200 text-slate-400 cursor-not-allowed'}`}
        >
          {isReady ? (
            <><RefreshCw className="w-4 h-4 animate-spin" /> 多智能体博弈寻优中...</>
          ) : (
            <>{historyLength > 0 ? "追加诉求并迭代路书" : "锁定意图并开始推演"} <ArrowRight className="w-4 h-4" /></>
          )}
        </button>
      </div>
    </motion.div>
  );
}

function DeductionPanel({ latestLog }: { latestLog?: string }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center py-20 h-full text-center px-8">
      <div className="relative w-24 h-24 mb-6">
        <motion.div animate={{ rotate: 360 }} transition={{ duration: 3, repeat: Infinity, ease: "linear" }} className="absolute inset-0 border-2 border-dashed border-orange-400 rounded-full" />
        <div className="absolute inset-0 flex items-center justify-center">
          <RefreshCw className="w-6 h-6 text-orange-500 animate-spin" />
        </div>
      </div>
      <h3 className="text-base font-black text-slate-800">多智能体正在博弈调和众口诉求...</h3>
      
      <div className="mt-4 p-3.5 rounded-2xl bg-slate-900 text-slate-200 text-xs font-mono max-w-sm w-full border border-slate-800 shadow-md">
        <div className="flex items-center gap-1.5 text-orange-400 font-bold mb-1">
          <span className="w-2 h-2 rounded-full bg-orange-500 animate-ping"></span>
          <span>智能体实时推演中:</span>
        </div>
        <p className="text-[11px] text-slate-300 leading-relaxed line-clamp-2">
          {latestLog || "全息雷达数据已捕获，多智能体正在计算拓扑并交织生成行程..."}
        </p>
      </div>
    </motion.div>
  );
}

function TeamPresenceBar({ members = [], teamSatisfaction = {}, roomCode, arbitrationRecords = [] }: any) {
  const avgSatisfaction = useMemo(() => {
    const scores = Object.values(teamSatisfaction).map(v => Number(v) || 90);
    return scores.length > 0 ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : 94;
  }, [teamSatisfaction]);

  return (
    <div className="mb-6 p-4 rounded-3xl bg-slate-900 text-white border border-slate-800 shadow-xl">
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-800">
        <div className="flex items-center gap-2.5">
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="font-black text-sm text-slate-100">同行网络协同沙盘 ({members.length}人)</span>
          <span className="text-[10px] bg-orange-500/20 text-orange-400 border border-orange-500/30 px-2 py-0.5 rounded-full font-mono font-bold">
            ROOM: {roomCode}
          </span>
        </div>
        
        <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1 rounded-xl">
          <span className="text-xs text-slate-400 font-bold">团队共识达成率:</span>
          <span className="text-sm font-black text-emerald-400 font-mono">{avgSatisfaction}%</span>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 mt-3">
        {members.map((m: any, idx: number) => {
          const matchedKey = Object.keys(teamSatisfaction).find(k => k.includes(m.name) || k.includes(m.role));
          const satScore = matchedKey ? teamSatisfaction[matchedKey] : (92 + (idx % 5));
          return (
            <div key={idx} className="bg-slate-800/80 p-2.5 rounded-2xl border border-slate-700/80 flex items-center justify-between">
              <div className="flex items-center gap-2 overflow-hidden">
                <img 
                  src={`https://api.dicebear.com/9.x/avataaars/svg?seed=${m.avatarSeed || m.name}&backgroundColor=b6e3f4`} 
                  alt={m.name} 
                  className="w-8 h-8 rounded-full bg-slate-700 border border-slate-600 shrink-0"
                />
                <div className="overflow-hidden">
                  <div className="flex items-center gap-1">
                    <span className="font-black text-xs text-slate-200 truncate max-w-[70px]">{m.name}</span>
                    <span className="text-[9px] text-orange-400 bg-orange-500/10 px-1 py-0.2 rounded font-bold shrink-0">{m.role}</span>
                  </div>
                  <p className="text-[9px] text-slate-400 truncate max-w-[100px] mt-0.5">{m.intent}</p>
                </div>
              </div>

              <div className="text-right shrink-0 pl-1">
                <span className="text-[9px] text-slate-400 font-bold block">满意度</span>
                <span className="text-xs font-black text-emerald-400 font-mono">{satScore}%</span>
              </div>
            </div>
          );
        })}
      </div>

      {arbitrationRecords && arbitrationRecords.length > 0 && (
        <div className="mt-3 pt-3 border-t border-slate-800 text-[11px] space-y-1">
          <div className="flex items-center gap-1.5 text-orange-400 font-bold mb-1">
            <Scale className="w-3.5 h-3.5" />
            <span>多智能体纳什均衡裁决记录：</span>
          </div>
          {arbitrationRecords.map((rec: string, rIdx: number) => (
            <p key={rIdx} className="text-slate-300 pl-4 relative before:content-['•'] before:absolute before:left-1 before:text-orange-500">
              {rec}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function DynamicBudgetCard({ summary, budgetData }: { summary?: string; budgetData?: any }) {
  if (!summary && !budgetData) return null;

  const mode = budgetData?.budget_mode || 'VALUE_COST_EFFECTIVE';
  const total = budgetData?.total_budget || 0;

  return (
    <div className="mb-6 p-4 rounded-2xl bg-gradient-to-r from-emerald-50 to-teal-50 border border-emerald-200 dark:bg-slate-800 dark:border-slate-700 shadow-xs">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className={`p-1.5 rounded-lg font-bold text-xs text-white ${
            mode === 'HIGH_LUXURY' ? 'bg-purple-600' : mode === 'EXACT_AMOUNT' ? 'bg-emerald-600' : 'bg-orange-500'
          }`}>
            {mode === 'HIGH_LUXURY' ? '💎 臻选高预算' : mode === 'EXACT_AMOUNT' ? '💰 专属定额精算' : '✨ 高性价比方案'}
          </div>
          <h4 className="font-black text-sm text-slate-800 dark:text-white">
            {mode === 'EXACT_AMOUNT' ? `${total} 元行程预算分解` : mode === 'HIGH_LUXURY' ? '品质奢华花销拆解' : '性价比预估花销'}
          </h4>
        </div>
        {budgetData?.daily_avg > 0 && (
          <span className="text-[11px] font-mono text-emerald-700 dark:text-emerald-400 bg-emerald-100 dark:bg-slate-700 px-2 py-0.5 rounded-md font-bold">
            日均: ¥{budgetData.daily_avg}/天
          </span>
        )}
      </div>

      <p className="text-xs text-slate-600 dark:text-slate-300 leading-relaxed font-medium">
        {summary}
      </p>

      {budgetData && (
        <div className="grid grid-cols-4 gap-2 mt-3 text-center">
          <div className="bg-white/80 dark:bg-slate-900/60 p-2 rounded-xl border border-emerald-100 dark:border-slate-700">
            <p className="text-[10px] text-slate-400 font-bold">🏨 住宿预留</p>
            <p className="text-xs font-black text-emerald-600 dark:text-emerald-400 mt-0.5">¥{budgetData.hotel}</p>
          </div>
          <div className="bg-white/80 dark:bg-slate-900/60 p-2 rounded-xl border border-emerald-100 dark:border-slate-700">
            <p className="text-[10px] text-slate-400 font-bold">🍲 餐饮寻味</p>
            <p className="text-xs font-black text-orange-500 mt-0.5">¥{budgetData.dining}</p>
          </div>
          <div className="bg-white/80 dark:bg-slate-900/60 p-2 rounded-xl border border-emerald-100 dark:border-slate-700">
            <p className="text-[10px] text-slate-400 font-bold">🎟️ 门票体验</p>
            <p className="text-xs font-black text-indigo-500 mt-0.5">¥{budgetData.ticket}</p>
          </div>
          <div className="bg-white/80 dark:bg-slate-900/60 p-2 rounded-xl border border-emerald-100 dark:border-slate-700">
            <p className="text-[10px] text-slate-400 font-bold">🚗 交通备用</p>
            <p className="text-xs font-black text-slate-700 dark:text-slate-300 mt-0.5">¥{budgetData.traffic}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function ExpandableConnectorNav({ pt, nextPt, travelDetails }: { pt: any; nextPt: any; travelDetails: any }) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'driving' | 'transit' | 'walking'>('driving');

  const pairKey = `${pt.name}|${nextPt?.name}`;
  const reversePairKey = `${nextPt?.name}|${pt.name}`;
  const details = travelDetails?.[pairKey] 
                || travelDetails?.[reversePairKey] 
                || travelDetails?.[pt.name] 
                || travelDetails?.[nextPt?.name] 
                || {};

  const currentModeData = details[activeTab] || details[Object.keys(details)[0]] || {};

  const label = currentModeData?.label || '出行';
  // 绝不使用硬编码默认值：拿不到真实时长/距离就留空，由界面引导打开高德实景导航
  const durMin = currentModeData?.duration_min;
  const distKm = currentModeData?.distance_km;

  let steps: string[] = [];
  if (currentModeData?.steps && Array.isArray(currentModeData.steps) && currentModeData.steps.length > 0) {
    steps = currentModeData.steps
      .map((s: any) => {
        if (typeof s === 'string') return s.replace(/<\/?[^>]+(>|$)/g, '').trim();
        const inst = s?.instruction || s?.road || '';
        return inst.replace(/<\/?[^>]+(>|$)/g, '').trim();
      })
      .filter((s: string) => Boolean(s && s.length > 0));
  }

  // 👑 是否拿到高德真实导航数据；拿不到时绝不编造时长/距离，改为引导打开真实导航
  const hasRealNav = Object.keys(details || {}).length > 0;
  const fromLnglat = Array.isArray(pt?.lnglat) ? pt.lnglat : null;
  const toLnglat = Array.isArray(nextPt?.lnglat) ? nextPt.lnglat : null;
  let navHref = '';
  if (fromLnglat && fromLnglat.length >= 2 && toLnglat && toLnglat.length >= 2) {
    navHref = `https://uri.amap.com/navigation?from=${fromLnglat[0]},${fromLnglat[1]},${encodeURIComponent(pt.name)}&to=${toLnglat[0]},${toLnglat[1]},${encodeURIComponent(nextPt.name)}&mode=car`;
  } else if (nextPt?.amap_url) {
    navHref = nextPt.amap_url;
  }

  return (
    <div className="mt-8 mb-4 relative">
      <div 
        onClick={() => setIsOpen(!isOpen)}
        className="absolute -left-[27px] top-2.5 w-5 h-5 bg-white border border-slate-300 rounded-full flex items-center justify-center z-10 text-slate-400 hover:border-orange-500 hover:text-orange-500 cursor-pointer shadow-2xs transition-colors"
      >
        <ArrowDown className="w-3 h-3" />
      </div>

      <button
        onClick={() => setIsOpen(!isOpen)}
        className="ml-1 w-full flex items-center justify-between py-2.5 px-4 bg-slate-50 hover:bg-orange-50/80 rounded-2xl border border-slate-200/80 hover:border-orange-200 text-xs transition-all cursor-pointer shadow-2xs group"
      >
        <div className="flex items-center gap-2 text-slate-600">
          <span className="text-slate-400 font-medium">推荐前往【{nextPt.name}】：</span>
          {hasRealNav ? (
            <>
              <span className="font-bold text-slate-700 flex items-center gap-1">
                <Car className="w-3.5 h-3.5 text-orange-500 group-hover:scale-110 transition-transform"/> {label}
              </span>
              {(durMin != null || distKm != null) && (
                <>
                  <span className="text-slate-300">·</span>
                  <span className="font-bold text-slate-600">
                    {durMin != null ? `约 ${durMin} 分钟` : ''}{distKm != null ? ` (${distKm}km)` : ''}
                  </span>
                </>
              )}
            </>
          ) : (
            <span className="font-bold text-orange-600 flex items-center gap-1">
              <Navigation className="w-3.5 h-3.5" /> 打开高德实景导航
            </span>
          )}
        </div>

        <div className="flex items-center gap-1 text-[11px] font-bold text-orange-500">
          <span>{isOpen ? '收起路线' : (hasRealNav ? '展开多模态高德逐字导航' : '查看导航')}</span>
          {isOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </div>
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden mt-2 ml-1 bg-slate-900 text-slate-200 p-3.5 rounded-2xl text-xs space-y-3 border border-slate-800 shadow-lg"
          >
            <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
              {details.driving && (
                <button 
                  onClick={() => setActiveTab('driving')}
                  className={`px-3 py-1 rounded-lg font-bold text-[11px] flex items-center gap-1 transition-all ${activeTab === 'driving' ? 'bg-orange-500 text-white shadow-xs' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                >
                  🚗 驾车 ({details.driving.duration_min}分)
                </button>
              )}
              {details.transit && (
                <button 
                  onClick={() => setActiveTab('transit')}
                  className={`px-3 py-1 rounded-lg font-bold text-[11px] flex items-center gap-1 transition-all ${activeTab === 'transit' ? 'bg-orange-500 text-white shadow-xs' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                >
                  🚌 公交/地铁 ({details.transit.duration_min}分)
                </button>
              )}
              {details.walking && (
                <button 
                  onClick={() => setActiveTab('walking')}
                  className={`px-3 py-1 rounded-lg font-bold text-[11px] flex items-center gap-1 transition-all ${activeTab === 'walking' ? 'bg-orange-500 text-white shadow-xs' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                >
                  🚶 步行 ({details.walking.duration_min}分)
                </button>
              )}
            </div>

            <div className="font-bold text-orange-400 flex items-center gap-1.5 pt-0.5">
              <MapPin className="w-3.5 h-3.5 shrink-0" />
              <span>高德地图实测【{currentModeData.label || '出行'}】线路指引（{pt.name} → {nextPt.name}）：</span>
            </div>

            {steps.length > 0 ? (
              <div className="space-y-1.5 pt-1">
                {steps.map((step, idx) => (
                  <div key={idx} className="text-[11px] text-slate-300 flex items-start gap-2 leading-relaxed">
                    <span className="text-orange-400 font-bold shrink-0">{idx + 1}.</span>
                    <span>{step}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-slate-300 leading-relaxed space-y-2 pt-1">
                <div>该路段暂未获取到逐字导航指引。</div>
                {navHref ? (
                  <a
                    href={navHref}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-orange-500/90 hover:bg-orange-500 text-white font-bold transition-colors"
                  >
                    <Navigation className="w-3.5 h-3.5" /> 打开高德实时导航
                  </a>
                ) : (
                  <div className="text-slate-400">请使用【高德外链】查看该地点。</div>
                )}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function MafengwoStylePanel({ 
  routes, totalDays, activeDayIndex, onSelectDay, travelDetails, selectedPoiIndex, 
  onSelectPoi, weatherInfo, trafficInfo, onReset, consensusSummary, budgetData, 
  onSwapPoi, roomCode, roomMembers, teamSatisfaction, arbitrationRecords, nodeVotes, onVote 
}: any) {
  const [showWeatherForecast, setShowWeatherForecast] = useState(false);

  return (
    <div className="pt-2 pb-32 font-sans">
      <div className="px-8 pt-4">
        <TeamPresenceBar 
          members={roomMembers} 
          teamSatisfaction={teamSatisfaction} 
          roomCode={roomCode} 
          arbitrationRecords={arbitrationRecords} 
        />
      </div>

      <div className="flex items-center gap-2 px-8 mb-6 sticky top-0 bg-white/95 backdrop-blur-md py-4 z-20 border-b border-slate-100 overflow-x-auto custom-scrollbar">
        {totalDays.map((dayNum: number, idx: number) => (
          <button 
            key={`day-pill-btn-${dayNum}-${idx}`} 
            onClick={() => onSelectDay(idx)}
            className={`px-4 py-1.5 rounded-full text-xs font-bold border transition-all cursor-pointer whitespace-nowrap ${activeDayIndex === idx ? 'bg-orange-500 text-white border-orange-500 shadow-md shadow-orange-200 scale-105' : 'bg-white text-slate-600 border-slate-200 hover:border-orange-300 hover:text-orange-500'}`}
          >
            Day {dayNum}
          </button>
        ))}
      </div>

      <div className="px-8">
        <DynamicBudgetCard summary={consensusSummary} budgetData={budgetData} />

        {weatherInfo && (
          <div className="mb-8 p-4 bg-gradient-to-r from-orange-50/90 to-amber-50/60 border border-orange-100 rounded-2xl shadow-2xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-orange-500 text-white rounded-xl shadow-xs"><Sun className="w-5 h-5"/></div>
                <div>
                  <h4 className="text-sm font-black text-slate-800">目的地气象：{weatherInfo.condition}</h4>
                  <p className="text-xs text-slate-500 mt-0.5 font-medium">{trafficInfo?.advice || '实时路况良好，宜出行游览。'}</p>
                </div>
              </div>
              <button 
                onClick={() => setShowWeatherForecast(!showWeatherForecast)}
                className="text-xs font-bold text-orange-600 hover:text-orange-700 underline flex items-center gap-1 cursor-pointer"
              >
                {showWeatherForecast ? '收起预报' : '查看一周预报'}
              </button>
            </div>

            <AnimatePresence>
              {showWeatherForecast && (
                <motion.div key="weather-forecast-dropdown" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden mt-3 pt-3 border-t border-orange-200/60 grid grid-cols-4 gap-2">
                  {weatherInfo.forecast?.map((f: any, fIdx: number) => (
                    <div key={`forecast-card-${fIdx}`} className="bg-white/80 p-2 rounded-xl text-center border border-orange-100/60">
                      <p className="text-[10px] font-bold text-slate-400">{f.day}</p>
                      <p className="text-xs font-black text-slate-700 my-0.5">{f.text}</p>
                      <p className="text-[10px] font-bold text-orange-500">{f.temp}</p>
                    </div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        <h2 className="text-2xl font-black text-slate-800 mb-8 tracking-tight">第 {totalDays[activeDayIndex] || 1} 天 行程安排</h2>

        <div className="relative pl-6">
          <div className="absolute top-3 bottom-6 left-[11px] w-[2px] bg-slate-200/80"></div>

          {Array.isArray(routes) && routes.map((pt: any, idx: number) => {
            const isSelected = selectedPoiIndex === idx;

            const validPhotoUrls = (pt.photos || []).filter((u: string) => Boolean(u && (u.startsWith('http') || u.startsWith('//'))));
            const fallbackImg = getCleanPhotoUrl(pt.map_image || '');
            // 真实照片优先，其次高德坐标静态地图兜底（与实际地理位置严格一致），杜绝空白与张冠李戴
            const realPhotos = [
              ...validPhotoUrls.slice(0, 3).map((url: string) => getCleanPhotoUrl(url, pt.name, 0)),
              fallbackImg
            ].filter((u: string) => Boolean(u)).slice(0, 3);

            // 👑 地点详情链接：优先后端回填的 amap_url，缺失时回退到高德关键词搜索，保证“查看详情”永远可点击
            const detailUrl = (pt.amap_url && typeof pt.amap_url === 'string' && pt.amap_url.startsWith('http'))
              ? pt.amap_url
              : `https://www.amap.com/search?query=${encodeURIComponent(String(pt.name || ''))}`;

            return (
              <div key={`poi-node-card-${pt.name}-${idx}`} className="relative mb-10">
                <div 
                  onClick={() => onSelectPoi(idx)}
                  className={`absolute -left-[30px] top-0.5 w-7 h-7 rounded-full flex items-center justify-center text-[12px] font-black cursor-pointer transition-all z-10 ${isSelected ? 'bg-orange-500 text-white shadow-lg shadow-orange-300 ring-4 ring-orange-100 scale-110' : 'bg-white text-slate-500 border-2 border-slate-300 hover:border-orange-400'}`}
                >
                  {idx + 1}
                </div>

                <div className="cursor-pointer group" onClick={() => onSelectPoi(idx)}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2.5 flex-wrap">
                      <h3 className={`text-xl font-black transition-colors ${isSelected ? 'text-orange-500' : 'text-slate-800 group-hover:text-orange-500'}`}>{pt.name}</h3>
                      <span className="flex items-center gap-1 bg-orange-50 text-orange-600 px-2 py-0.5 rounded text-xs font-bold border border-orange-100">
                        ⭐ {pt.rating || '4.8'}
                      </span>

                      {pt.cost && (
                        <span className="flex items-center gap-1 bg-emerald-50 text-emerald-700 px-2.5 py-0.5 rounded-lg text-xs font-bold border border-emerald-200/80 shadow-2xs">
                          <PiggyBank className="w-3.5 h-3.5 text-emerald-600"/>
                          <span>预估花销：{pt.cost}</span>
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={(e) => { e.stopPropagation(); onVote(pt.name, 'up'); }}
                        className={`p-1.5 rounded-lg border text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer ${nodeVotes[pt.name] === 'up' ? 'bg-emerald-500 text-white border-emerald-500' : 'bg-slate-50 hover:bg-emerald-50 text-slate-500 hover:text-emerald-600 border-slate-200'}`}
                        title="赞同该节点安排"
                      >
                        <ThumbsUp className="w-3 h-3" />
                      </button>

                      <button
                        onClick={(e) => { e.stopPropagation(); onVote(pt.name, 'down'); }}
                        className={`p-1.5 rounded-lg border text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer ${nodeVotes[pt.name] === 'down' ? 'bg-rose-500 text-white border-rose-500' : 'bg-slate-50 hover:bg-rose-50 text-slate-500 hover:text-rose-600 border-slate-200'}`}
                        title="不满意？点此触发团队平替置换"
                      >
                        <ThumbsDown className="w-3 h-3" />
                      </button>

                      {onSwapPoi && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onSwapPoi(pt.name); }}
                          className="text-xs text-slate-500 hover:text-orange-600 font-bold flex items-center gap-1 bg-slate-100 hover:bg-orange-50 px-2.5 py-1 rounded-lg border border-slate-200 hover:border-orange-200 transition-colors cursor-pointer"
                          title="一键换成同城同类好去处"
                        >
                          <RotateCw className="w-3 h-3" />
                          <span>换一换</span>
                        </button>
                      )}

                      <a 
                        href={detailUrl} 
                        target="_blank" 
                        rel="noopener noreferrer" 
                        onClick={(e) => e.stopPropagation()}
                        className="text-xs text-orange-600 hover:text-orange-700 font-bold flex items-center gap-1 bg-orange-50/80 px-2.5 py-1 rounded-lg border border-orange-200/80 hover:bg-orange-100 transition-colors"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                        <span>查看详情</span>
                      </a>
                    </div>
                  </div>

                  {pt.time && (
                    <div className="mb-3 inline-flex items-center gap-1.5 text-xs font-bold text-orange-600 bg-orange-50 px-3 py-1 rounded-xl border border-orange-200/80 shadow-2xs">
                      <Clock className="w-3.5 h-3.5 text-orange-500" />
                      <span>建议游玩时段：{pt.time}</span>
                    </div>
                  )}

                  {(pt.open_time || pt.address) && (
                    <div className="mb-3 flex flex-wrap gap-2 text-[11px] font-medium text-slate-500">
                      {pt.open_time && (
                        <span className="inline-flex items-center gap-1 bg-slate-100 px-2.5 py-1 rounded-lg border border-slate-200">
                          <Clock className="w-3 h-3 text-slate-400" /> 开放时间：{pt.open_time}
                        </span>
                      )}
                      {pt.address && (
                        <span className="inline-flex items-center gap-1 bg-slate-100 px-2.5 py-1 rounded-lg border border-slate-200">
                          <MapPin className="w-3 h-3 text-slate-400" /> {pt.address}
                        </span>
                      )}
                    </div>
                  )}

                  {pt.split_info && (
                    <div className="mb-3 p-3 rounded-2xl bg-gradient-to-r from-purple-500/10 to-indigo-500/10 border border-purple-500/30 text-xs">
                      <div className="font-bold text-purple-700 dark:text-purple-400 flex items-center gap-1.5 mb-1">
                        <Split className="w-3.5 h-3.5" />
                        <span>多智能体分合流时空调度：</span>
                      </div>
                      <p className="text-[11px] text-slate-600 dark:text-slate-300 leading-relaxed font-medium">
                        {pt.split_info}
                      </p>
                    </div>
                  )}

                  <div className="grid grid-cols-3 gap-2 mb-4">
                    {realPhotos.length > 0 ? realPhotos.map((src: string, imgIdx: number) => (
                      <div key={`img-thumb-${idx}-${imgIdx}`} className="rounded-xl overflow-hidden aspect-[4/3] bg-slate-100 border border-slate-100 shadow-2xs">
                         <PoiImage src={src} fallback={fallbackImg} name={pt.name} className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105" />
                      </div>
                    )) : (
                      <div className="col-span-3 rounded-xl overflow-hidden aspect-[4/3] bg-slate-100 border border-slate-100 shadow-2xs">
                        <PoiImage src="" fallback="" name={pt.name} className="w-full h-full object-cover" />
                      </div>
                    )}
                  </div>

                  <div className="text-sm text-slate-600 leading-relaxed space-y-2.5 mb-4">
                    <p className="leading-loose"><span className="font-bold text-slate-800">体验与文化：</span>{pt.desc}</p>
                    
                    {pt.tags && pt.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {pt.tags.map((tag: string, tIdx: number) => <span key={`tag-pill-${idx}-${tIdx}`} className="bg-orange-50/80 border border-orange-100 text-orange-600 text-[11px] px-2 py-0.5 rounded-md font-bold">{tag}</span>)}
                      </div>
                    )}
                  </div>

                  {(pt.is_hotel || pt.tags?.includes("住宿") || idx === routes.length - 1) && pt.hotel_candidates && pt.hotel_candidates.length > 0 && (
                    <div className="mt-3 p-3.5 rounded-2xl bg-purple-50/80 border border-purple-200/80 text-xs">
                      <div className="font-bold text-purple-900 mb-2 flex items-center justify-between">
                        <span className="flex items-center gap-1.5"><Hotel className="w-4 h-4 text-purple-600" /> 同商圈备选品质酒店：</span>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        {pt.hotel_candidates.map((cand: any, cIdx: number) => {
                          const candName = typeof cand === 'string' ? cand : (cand?.name || '酒店');
                          const candPrice = (typeof cand === 'object' && cand?.price) ? cand.price : '';
                          // 👑 稳健生成详情外链：对象无 amap_url 时回退到高德关键词搜索并对中文 URL 编码，杜绝 href=undefined 导致的点击无响应
                          const candUrl = (typeof cand === 'object' && typeof cand?.amap_url === 'string' && cand.amap_url)
                            ? cand.amap_url
                            : `https://www.amap.com/search?query=${encodeURIComponent(candName)}`;
                          const openHotelDetail = (e: React.MouseEvent) => {
                            e.stopPropagation();
                            e.preventDefault();
                            if (candUrl) window.open(candUrl, '_blank', 'noopener,noreferrer');
                          };
                          return (
                            <div key={cIdx} className="bg-white p-2.5 rounded-xl border border-purple-100 flex items-center justify-between shadow-2xs">
                              <span className="font-bold text-slate-800 text-xs truncate max-w-[130px]">{candName} {candPrice && <span className="text-purple-600 font-normal">({candPrice})</span>}</span>
                              <div className="flex items-center gap-1.5 shrink-0">
                                <a 
                                  href={candUrl} 
                                  target="_blank" 
                                  rel="noopener noreferrer" 
                                  onClick={openHotelDetail}
                                  className="px-2 py-1 text-purple-600 hover:bg-purple-50 rounded-md border border-purple-200 text-[11px] font-bold flex items-center gap-0.5 cursor-pointer"
                                >
                                  <ExternalLink className="w-3.5 h-3.5" /> 详情
                                </a>
                                <button 
                                  onClick={(e) => { e.stopPropagation(); onSwapPoi(pt.name); }} 
                                  className="px-2 py-1 bg-purple-600 hover:bg-purple-700 text-white rounded-md text-[11px] font-bold flex items-center gap-0.5 cursor-pointer"
                                >
                                  <RotateCw className="w-3.5 h-3.5" /> 置换
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>

                {idx < routes.length - 1 && (
                  <ExpandableConnectorNav 
                    pt={pt} 
                    nextPt={routes[idx + 1]} 
                    travelDetails={travelDetails} 
                  />
                )}

              </div>
            );
          })}
        </div>

        <div className="mt-8 pt-6 border-t border-slate-100 flex justify-center">
          <button 
            onClick={onReset} 
            className="px-6 py-3 bg-slate-900 border border-slate-800 text-white rounded-xl font-bold text-sm flex items-center gap-2 hover:bg-orange-500 transition-all shadow-md cursor-pointer"
          >
            <Wand2 className="w-4 h-4" /> 对当前路线增量调优
          </button>
        </div>
      </div>
    </div>
  );
}

function BottomMapCarousel({ selectedIndex, onSelect, routes }: any) {
  if (!routes || routes.length === 0) return null;
  
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    if (scrollContainerRef.current && selectedIndex !== null) {
      const container = scrollContainerRef.current;
      const child = container.children[selectedIndex] as HTMLElement;
      if (child) {
        const scrollLeft = child.offsetLeft - container.offsetWidth / 2 + child.offsetWidth / 2;
        container.scrollTo({ left: scrollLeft, behavior: 'smooth' });
      }
    }
  }, [selectedIndex]);

  return (
    <div className="w-full bg-gradient-to-t from-slate-900/80 via-slate-900/30 to-transparent pt-12 pb-6 px-8 backdrop-blur-[1px]">
      <div ref={scrollContainerRef} className="flex gap-4 overflow-x-auto custom-scrollbar pb-2 snap-x snap-mandatory max-w-6xl mx-auto">
        {routes.map((r: any, idx: number) => {
          const isSelected = selectedIndex === idx;
          const photoUrl = getCleanPhotoUrl(
            (r.photos && r.photos.length > 0) ? String(r.photos[0]) : (r.map_image || '')
          );

          return (
            <div
              key={`carousel-card-item-${r.name}-${idx}`}
              onClick={() => onSelect(idx)}
              className={`snap-center shrink-0 w-[200px] h-[120px] rounded-2xl overflow-hidden relative cursor-pointer transition-all duration-300 transform bg-white ${isSelected ? 'ring-4 ring-orange-500 ring-offset-2 ring-offset-slate-900 scale-105 shadow-2xl z-10' : 'opacity-85 hover:opacity-100 hover:scale-102 shadow-md'}`}
            >
              {photoUrl ? (
                <img 
                  src={photoUrl} 
                  onError={(e) => {
                    const img = e.target as HTMLImageElement;
                    const fallback = getCleanPhotoUrl(r.map_image || '');
                    if (fallback && img.src !== fallback) {
                      img.src = fallback;
                    } else {
                      img.style.display = 'none';
                    }
                  }}
                  className="absolute inset-0 w-full h-full object-cover" 
                  alt={r.name}
                  referrerPolicy="no-referrer"
                />
              ) : (
                <div className="absolute inset-0 w-full h-full bg-gradient-to-br from-orange-500 to-rose-600 flex items-center justify-center">
                  <span className="text-white font-black text-3xl drop-shadow">{String(r.name || '景').charAt(0)}</span>
                </div>
              )}
              
              <div className="absolute inset-x-0 bottom-0 h-2/3 bg-gradient-to-t from-black/90 via-black/40 to-transparent pointer-events-none" />
              
              <div className={`absolute top-2.5 left-2.5 w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-black shadow-sm ${isSelected ? 'bg-orange-500 text-white' : 'bg-white/90 text-slate-800'}`}>
                {idx + 1}
              </div>
              
              <div className="absolute bottom-2.5 left-3 right-3 pointer-events-none">
                <h4 className="text-white font-black text-sm truncate drop-shadow-md">{r.name}</h4>
                <p className="text-orange-200 text-[10px] font-bold mt-0.5 truncate">{r.tags?.[0] || '热门景点'}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}