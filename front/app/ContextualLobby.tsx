'use client';

import dynamic from 'next/dynamic';
import React, { useState, useRef, useEffect, useMemo } from 'react';
import { 
  Users, MapPin, Sparkles, Coffee, Camera, Car, PiggyBank, ArrowRight, Check, 
  UserPlus, Map as MapIcon, Compass, Headphones, TrendingDown, RefreshCw, 
  X, AlertCircle, Clock, ThumbsUp, ThumbsDown, BrainCircuit,
  MessageSquare, Wand2, ArrowDown, Sun, ExternalLink, Hotel, RotateCw, Split, Scale,
  Lock, User, LogIn, LogOut, Edit3, KeyRound, CheckCircle2, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, Copy,
  Compass as CompassIcon, ShieldCheck, ArrowLeft, CalendarDays, Route, Save, Shuffle,
  CalendarClock, Heart, Send, Plus, MessageCircle, Flame, Mic, Navigation,
  Trophy, Award, Medal, Star, Mountain, UtensilsCrossed, Landmark, Quote, BookOpen, Footprints,
  Activity, Radio, Wifi, Zap, Signal, Share2, Link2, Download, Train
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { API_BASE, WS_BASE, apiFetch, getAuthToken, installApiFetchInterceptor } from './lib/utils';

// 实景图片组件（轻量独立文件，避免捆入重型 3D 地图 chunk）
import RealPoiImage from './components/RealPoiImage';

// 自定义头像上传与裁剪组件
import AvatarUploader from './components/AvatarUploader';
import ModernProfileScreen from './components/ProfileScreen';
import CommunityPanel from './components/CommunityPanel';

// 一键分享行程海报
import ItineraryPoster from './components/ItineraryPoster';

// 旅中风险推送中心：轮询实时风险快照，检测天气/路况/安全事件变更并推送
import RiskPushCenter from './components/RiskPushCenter';

// 碳足迹与绿色交通评估（P5）
import CarbonFootprintPanel from './components/CarbonFootprintPanel';
import ConsensusExplainability from './components/ConsensusExplainability';
import ScenarioSimulator from './components/ScenarioSimulator';
import TripDiaryModal from './components/TripDiaryModal';
import BudgetPanel from './components/BudgetPanel';
import ExpenseReportModal from './components/ExpenseReportModal';
import SatisfactionModal from './components/SatisfactionModal';
import TransportSearch from './components/TransportSearch';
import { buildIcsCalendar, buildDiaryMarkdown } from './lib/tripExport';
import SecondaryPlanner from './components/SecondaryPlanner';
import PreTripPlanningPanel from './components/PreTripPlanningPanel';
import BookingHub from './components/BookingHub';
import TripExecutionPanel from './components/TripExecutionPanel';
import ReplanProposalPanel from './components/ReplanProposalPanel';

// 目的地图谱数据（用于主页灵感目的地推荐）
import { PROVINCE_DATA } from './data/provinceData';

// 模块 9：语音结构化参数解析
import { parseVoiceIntent } from './lib/voiceIntent';
import VoiceIntentChips from './components/VoiceIntentChips';

// 精简行政区名称（如 "西藏自治区" -> "西藏"）
function shortenRegionName(full: string) {
  return full.replace(/(壮族自治区|回族自治区|维吾尔自治区|特别行政区|自治区|省|市)$/, '');
}

// 全站评分最高的目的地（用于主页灵感目的地图谱卡片）
const TOP_DESTINATIONS = Object.entries(PROVINCE_DATA)
  .sort((a, b) => parseFloat(b[1].summary.score) - parseFloat(a[1].summary.score))
  .slice(0, 6)
  .map(([name]) => name);

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

// 全域路由调度器（3D 地图 + WebGL 重型依赖，按需懒加载避免首屏同步解析卡死）
const FullRouteVisualizer = dynamic(
  () => import('./FullRouteVisualizer'),
  {
    ssr: false,
    loading: () => (
      <div className="relative w-full h-full bg-slate-100 flex flex-col items-center justify-center gap-3">
        <RefreshCw className="w-6 h-6 text-orange-500 animate-spin" />
        <span className="text-sm font-bold text-slate-400 tracking-wider">全域路由调度器装载中...</span>
      </div>
    )
  }
);

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
          lnglat: normalizeLnglat(item.lnglat),
          coordinate_status: normalizeLnglat(item.lnglat) ? 'verified' : 'missing',
          color: item.tags?.includes("寻味") || item.type === "food" ? "#f97316" : "#3b82f6",
          desc: item.desc || item.action || "",
          time: item.time || "",
          time_reason: item.time_reason || "",
          transport: item.transport || "",
          tags: item.tags || [],
          cost: item.cost_estimate || item.cost || "暂无供应商数据",
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

// 👑 智能POI图片组件：5级兜底链保证100%视觉覆盖，永不空白
// 实景图轮询[0→1→2] → 静态坐标地图 → 精美地图预览卡片（带跳转）
interface PoiImageProps {
  photos?: string[];       // 实景照片URL数组（自动轮询）
  mapImage?: string;       // 高德静态地图兜底
  amapUrl?: string;        // 高德地图跳转链接（用于兜底卡片）
  name: string;            // 地点名称
  type?: string;           // 地点类型（风景/餐饮/住宿等）
  className?: string;
  index?: number;          // 序号
  onPhotoClick?: () => void; // 点击兜底卡片时触发（打开实景照片搜索）
}
function PoiImage({ photos = [], mapImage = '', amapUrl = '', name, type = '', className = '', index, onPhotoClick }: PoiImageProps) {
  // 👑 只使用真实实景照片，绝不用高德静态地图充数（静态地图≠实景照片，用户无法了解地点实况）
  const queue = React.useMemo(() => {
    const urls: string[] = [];
    (Array.isArray(photos) ? photos : []).forEach(p => {
      const u = getCleanPhotoUrl(p);
      if (u && !urls.includes(u)) urls.push(u);
    });
    // mapImage（高德静态坐标地图）不再加入图片队列——它只是一张带红点的地图截图，
    // 无法让用户看到地点的实际面貌。没有实景照片时直接展示兜底卡片。
    return urls;
  }, [photos]);

  const [currentIdx, setCurrentIdx] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [allFailed, setAllFailed] = useState(queue.length === 0);

  const currentUrl = !allFailed && currentIdx < queue.length ? queue[currentIdx] : '';

  const handleError = React.useCallback(() => {
    setLoaded(false);
    if (currentIdx + 1 < queue.length) {
      setCurrentIdx(currentIdx + 1);
    } else {
      setAllFailed(true);
    }
  }, [currentIdx, queue.length]);

  // 全部失败时：显示精美地点卡片（无实景照片时绝不用地图截图充数）
  if (allFailed || !currentUrl) {
    const typeLabel = type || '目的地';
    const typeColor = type?.includes('餐') || type?.includes('食') ? 'from-orange-400 to-rose-400'
      : type?.includes('酒店') || type?.includes('住宿') ? 'from-indigo-400 to-purple-400'
      : 'from-teal-400 to-emerald-400';
    return (
      <div
        className={`relative overflow-hidden cursor-pointer group ${className}`}
        onClick={onPhotoClick}
        title="点击搜索该地点实景照片"
      >
        <div className={`absolute inset-0 bg-gradient-to-br ${typeColor} transition-transform duration-300 group-hover:scale-105`} />
        <div className="absolute inset-0 opacity-[0.15]" style={{backgroundImage: 'radial-gradient(circle at 2px 2px, rgba(255,255,255,0.4) 1px, transparent 0)', backgroundSize: '20px 20px'}} />
        <div className="relative h-full w-full flex flex-col items-center justify-center gap-2 p-4">
          {typeof index === 'number' && (
            <div className="absolute top-2 left-2 w-6 h-6 rounded-full bg-white/25 backdrop-blur-sm flex items-center justify-center text-white text-[11px] font-black">{index + 1}</div>
          )}
          <div className="w-12 h-12 rounded-full bg-white/20 backdrop-blur-sm flex items-center justify-center group-hover:bg-white/30 transition-colors">
            <Camera className="w-6 h-6 text-white" strokeWidth={2} />
          </div>
          <div className="text-center">
            <p className="text-white font-bold text-sm leading-tight px-2 line-clamp-2 drop-shadow-md">{name}</p>
            <p className="text-white/80 text-[10px] font-bold mt-1 tracking-wide">{typeLabel} · 暂无实景照片</p>
          </div>
          {onPhotoClick && (
            <div className="mt-1 px-3 py-1 rounded-full bg-white/20 backdrop-blur-sm text-white text-[10px] font-bold group-hover:bg-white/35 transition-colors flex items-center gap-1">
              <ExternalLink className="w-3 h-3" /> 去搜实景照片
            </div>
          )}
          {!onPhotoClick && amapUrl && (
            <a
              href={amapUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="mt-1 px-3 py-1 rounded-full bg-white/25 backdrop-blur-sm text-white text-[10px] font-bold hover:bg-white/40 transition-colors flex items-center gap-1"
            >
              <ExternalLink className="w-3 h-3" /> 在高德地图中查看
            </a>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`relative overflow-hidden bg-slate-100 dark:bg-slate-800 ${className}`}>
      {!loaded && (
        <div className="absolute inset-0 bg-gradient-to-br from-slate-200 to-slate-300 dark:from-slate-700 dark:to-slate-800 animate-pulse" />
      )}
      <img
        src={currentUrl}
        alt={name}
        onLoad={() => setLoaded(true)}
        onError={handleError}
        className={`w-full h-full object-cover transition-opacity duration-500 ${loaded ? 'opacity-100' : 'opacity-0'}`}
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    </div>
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
  token?: string;
  isLoggedIn: boolean;
}

export interface RoomMember {
  id: string;
  name: string;
  role: string;
  intent: string;
  avatarSeed: string;
  avatarUrl?: string;
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

// =========================================================================
// 主入口：认证判断 -> 大厅界面 -> 多智能体决策工作区
// =========================================================================
export default function ContextualLobby() {
  const [selectedMode, setSelectedMode] = useState<string>('coop');
  const [showPreferenceModal, setShowPreferenceModal] = useState(false);
  const [showEditIntentModal, setShowEditIntentModal] = useState(false);

  const [selectedRole, setSelectedRole] = useState<string>('寻味探索');
  const [myIntent, setMyIntent] = useState<string>('必须安排当地特色正宗老字号，不吃预制菜');
  const [roomCode, setRoomCode] = useState<string>('');
  const [inputRoomCode, setInputRoomCode] = useState<string>('');
  const [roomReady, setRoomReady] = useState(false);
  const [roomError, setRoomError] = useState('');
  
  const [isConnecting, setIsConnecting] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [showCommunity, setShowCommunity] = useState(false);
  const [showTransportSearch, setShowTransportSearch] = useState(false);
  const [showBookingHub, setShowBookingHub] = useState(false);
  const [showDestinationMap, setShowDestinationMap] = useState(false);
  const [planDestination, setPlanDestination] = useState<string | null>(null);

  // 本地持久化用户状态 (与 SQLite 后端鉴权打通)
  // 改为在客户端挂载后再读取 localStorage，避免 SSR 与 CSR 首次渲染不一致导致 Hydration 报错
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const currentUserRef = useRef<UserProfile | null>(null); // 👑 始终保持最新 currentUser，解决 WebSocket 闭包陈旧问题
  const [authReady, setAuthReady] = useState(false);

  // 房间真实成员列表（由 WebSocket 广播集中下发同步）
  const [roomMembers, setRoomMembers] = useState<RoomMember[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  // Authoritative "the collaboration socket is open" signal. Set by the room
  // socket's onopen/onclose handlers; used to gate workspace entry instead of a
  // guessed duration.
  const [roomSocketReady, setRoomSocketReady] = useState(false);
  const roomSocketReadyRef = useRef(false);
  const [copied, setCopied] = useState(false);

  // 挂载后从 localStorage 恢复登录态（仅在浏览器端执行）
  useEffect(() => {
    installApiFetchInterceptor();
    try {
      const saved = localStorage.getItem('omni_user');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed && parsed.isLoggedIn) setCurrentUser(parsed);
      }
    } catch (e) {}
    setAuthReady(true);
  }, []);

  // 👑 保持 currentUserRef 与 currentUser 始终同步，解决 WebSocket 闭包陈旧问题
  useEffect(() => {
    currentUserRef.current = currentUser;
  }, [currentUser]);

  // 房间是真实的后端资源：首次登录自动创建并保存邀请码，避免仅在
  // 浏览器里随机生成一个其他设备无法加入的“假房间”。
  useEffect(() => {
    if (!currentUser) return;
    let cancelled = false;
    const bootstrapRoom = async () => {
      setRoomError('');
      try {
        const saved = localStorage.getItem(`omni_room_${currentUser.id}`);
        if (saved) {
          const parsed = JSON.parse(saved);
          if (parsed?.room_id && parsed?.invite_code) {
            if (!cancelled) {
              setRoomCode(String(parsed.room_id));
              setRoomReady(true);
            }
            return;
          }
        }
        const res = await apiFetch(`${API_BASE}/api/v1/room/create`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: currentUser.username, room_name: `${currentUser.nickname || currentUser.username} 的旅行计划`, role: '主理人' })
        });
        const data = await res.json();
        if (!res.ok || !data.room_id) throw new Error(data.error || '协作房间创建失败');
        localStorage.setItem(`omni_room_${currentUser.id}`, JSON.stringify(data));
        if (!cancelled) {
          setRoomCode(String(data.room_id));
          setRoomReady(true);
        }
      } catch (error: any) {
        if (!cancelled) {
          setRoomReady(false);
          setRoomError(error?.message || '协作房间暂时不可用');
        }
      }
    };
    bootstrapRoom();
    return () => { cancelled = true; };
  }, [currentUser]);

  // WebSocket 维持房间连接与在线成员同步
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    const connectWs = () => {
      // A rebuilt socket is not yet ready, so clear the flag up front. Without
      // this the flag could stay true from the previous connection and let the
      // user into a workspace whose socket is still handshaking.
      roomSocketReadyRef.current = false;
      if (!isMounted || !currentUser || !roomReady || !roomCode) return;
      const url = `${WS_BASE}/ws?room_id=${encodeURIComponent(roomCode)}&access_token=${encodeURIComponent(getAuthToken())}&user_id=${encodeURIComponent(currentUser.id)}&nickname=${encodeURIComponent(currentUser.nickname)}&role=${encodeURIComponent(selectedRole)}&intent=${encodeURIComponent(myIntent)}${currentUser.avatarUrl ? '&avatar_url=' + encodeURIComponent(currentUser.avatarUrl) : ''}`;
      ws = new WebSocket(url);

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "room_members_update") {
            const payload = msg.payload || [];
            // 👑 将当前用户的真实头像注入到成员列表中（用 ref 获取最新值，解决闭包陈旧问题）
            const cu = currentUserRef.current;
            const enriched = payload.map((m: any) => {
              if (m.id === cu?.id && cu?.avatarUrl) {
                return { ...m, avatarUrl: cu.avatarUrl };
              }
              return m;
            });
            setRoomMembers(enriched);
          }
        } catch (err) {}
      };

      ws.onopen = () => {
        if (!isMounted) return;
        roomSocketReadyRef.current = true;
        setRoomSocketReady(true);
      };
      ws.onclose = () => {
        roomSocketReadyRef.current = false;
        setRoomSocketReady(false);
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
  }, [roomCode, roomReady, currentUser, selectedRole, myIntent]);

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

  const handleBeginPlan = () => {
    // The preference dialog is a local interaction and must remain usable while
    // the collaboration room is still bootstrapping. The workspace will show
    // a recoverable connection state if the room is not ready yet.
    if (!roomReady) setRoomError('协作房间仍在同步，先完成偏好设置即可进入规划');
    setShowPreferenceModal(true);
  };

  const handleStartPlanFromDestination = (destination: string) => {
    setPlanDestination(destination);
    setShowDestinationMap(false);
    setShowPreferenceModal(true); // 复用现有「定制主控体验」流程
  };

  // Opens the workspace once the collaboration socket is genuinely usable.
  //
  // This used to be a fixed `setTimeout(..., 1000)`: it declared the app
  // "ready" merely because a second had elapsed. If the socket was still
  // handshaking the user landed in a workspace that silently could not send
  // anything, and if the socket was already open the user still waited a full
  // second for nothing. Readiness is now derived from WebSocket.OPEN, with an
  // explicit error path and a bounded timeout as the only escape hatch.
  // Opens the workspace once the collaboration socket is genuinely usable.
  //
  // This used to be a fixed `setTimeout(..., 1000)`: it declared the app
  // "ready" merely because a second had elapsed. If the socket was still
  // handshaking the user landed in a workspace that silently could not send
  // anything, and if the socket was already open the user still waited a full
  // second for nothing.
  //
  // Readiness now comes from roomSocketReady, which the room socket's own
  // onopen/onclose handlers maintain. That is the authoritative signal: the ref
  // alone is not reliable because it is only assigned after the connection
  // object is handed back, and reading WebSocket.OPEN on a mocked or absent
  // global yields undefined instead of 1.
  const enterWorkspace = () => {
    setShowPreferenceModal(false);
    setIsTransitioning(false);
    setIsConnecting(false);
    setRoomError('');
    setShowWorkspace(true);
  };

  const handleConfirmSync = () => {
    if (roomSocketReady) {
      enterWorkspace();
      return;
    }

    setIsConnecting(true);
    setRoomError('正在连接协同网络…');

    // Wait for the readiness flag rather than for a fixed duration, but never
    // wait forever: a socket that cannot come up must surface an error.
    let settled = false;
    let pollId = 0;
    let timeoutId = 0;
    const finish = (ok: boolean) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      window.clearInterval(pollId);
      if (ok) {
        enterWorkspace();
      } else {
        setIsConnecting(false);
        setRoomError('协同网络连接超时，请检查网络后重试');
      }
    };

    timeoutId = window.setTimeout(() => finish(false), 8000);

    // The flag lives in a ref as well so this poll does not need to re-create
    // itself on every state change.
    pollId = window.setInterval(() => {
      if (roomSocketReadyRef.current) finish(true);
    }, 120);
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

  const handleJoinRoom = async () => {
    const inviteCode = inputRoomCode.trim().toUpperCase();
    if (!inviteCode || !currentUser) return;
    setRoomError('');
    try {
      const res = await apiFetch(`${API_BASE}/api/v1/room/join`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: currentUser.username, invite_code: inviteCode, role: selectedRole })
      });
      const data = await res.json();
      if (!res.ok || !data.room_id) throw new Error(data.error || '加入房间失败');
      localStorage.setItem(`omni_room_${currentUser.id}`, JSON.stringify({ room_id: data.room_id, invite_code: inviteCode, room_name: data.room_name }));
      setRoomCode(String(data.room_id));
      setRoomReady(true);
      setInputRoomCode('');
    } catch (error: any) {
      setRoomError(error?.message || '加入房间失败');
    }
  };

  const handleCopyRoomCode = async () => {
    try {
      await navigator.clipboard.writeText(roomCode);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {}
  };

  const handleLogout = () => {
    // Revoke the server session before clearing the local principal. The
    // cleanup remains local even when the gateway is temporarily unavailable.
    void apiFetch(`${API_BASE}/api/auth/logout`, { method: 'POST' }).catch(() => undefined).finally(() => {
      localStorage.removeItem('omni_user');
      setCurrentUser(null);
    });
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
                  <span data-testid="room-code" className="text-base font-black text-orange-600 font-mono tracking-wider flex items-center gap-1.5">
                    {roomReady ? roomCode : '正在准备…'}
                    <button onClick={handleCopyRoomCode} className="p-1 hover:bg-orange-50 rounded-md transition-colors cursor-pointer" title="复制邀请码">
                      {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5 text-orange-500" />}
                    </button>
                  </span>
                </div>
                {roomError && <p className="text-[11px] text-rose-500 font-bold mb-2">{roomError}</p>}
                <div className="flex gap-1.5 mt-2">
                  <input data-testid="join-room-code"
                    type="text" 
                    placeholder="输入好友分享的房间码"
                    value={inputRoomCode}
                    onChange={(e) => setInputRoomCode(e.target.value)}
                    className="flex-1 px-3 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs outline-none focus:border-orange-500 font-mono uppercase font-bold text-slate-800"
                  />
                  <button data-testid="join-room-submit" onClick={handleJoinRoom} disabled={!inputRoomCode.trim()} className="px-3.5 py-1.5 bg-slate-900 hover:bg-orange-500 disabled:bg-slate-300 text-white rounded-lg text-xs font-bold transition-colors cursor-pointer disabled:cursor-not-allowed">
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
                    <img src={member.avatarUrl ? `${API_BASE}${member.avatarUrl}` : `https://api.dicebear.com/9.x/avataaars/svg?seed=${member.avatarSeed || member.name}&backgroundColor=fdeed8`} alt="avatar" className="w-9 h-9 rounded-full bg-slate-100 border border-slate-200 shrink-0 object-cover" />
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
      <ModernProfileScreen 
        currentUser={currentUser}
        onBack={() => setShowProfile(false)}
        onContinuePlanning={() => { setShowProfile(false); setShowWorkspace(true); }}
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
      <CommunityPanel 
        currentUser={currentUser}
        onBack={() => setShowCommunity(false)}
        onAdoptPlanning={(city) => { setPlanDestination(city); setShowCommunity(false); setShowWorkspace(true); }}
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

                <button 
                  onClick={() => setShowTransportSearch(true)}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 bg-emerald-50 border border-emerald-200 text-emerald-600 rounded-full text-xs font-bold hover:bg-emerald-100 transition-colors cursor-pointer"
                >
                  <Train className="w-3.5 h-3.5" /> 订票查询
                </button>

                <button
                  onClick={() => setShowBookingHub(true)}
                  className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-bold text-slate-600 transition-colors hover:border-orange-200 hover:bg-orange-50 hover:text-orange-600 cursor-pointer"
                >
                  <ExternalLink className="w-3.5 h-3.5" /> 官方渠道
                </button>

                <div className="flex items-center gap-3 bg-white border border-slate-200/80 px-3 py-1.5 rounded-2xl shadow-xs">
                  <div className="w-8 h-8 rounded-full overflow-hidden bg-orange-100 border border-orange-200">
                    <img src={currentUser.avatarUrl ? `${API_BASE}${currentUser.avatarUrl}` : `https://api.dicebear.com/9.x/avataaars/svg?seed=${currentUser.avatarSeed}&backgroundColor=fdeed8`} alt="avatar" className="w-full h-full object-cover" />
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
              <button data-testid="begin-plan" onClick={handleBeginPlan} className="ml-auto px-8 py-3.5 bg-orange-500 text-white rounded-2xl font-bold text-sm flex items-center justify-center gap-3 hover:bg-orange-600 shadow-lg shadow-orange-200/50 hover:-translate-y-0.5 transition-all duration-300 cursor-pointer">
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
          <motion.div key="auth-modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-slate-950/45 backdrop-blur-md z-[100] flex items-center justify-center p-4">
            <motion.div key="auth-modal-dialog" initial={{ scale: 0.96, y: 18 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.96, y: -18 }} className="bg-white rounded-2xl w-full max-w-2xl overflow-hidden shadow-2xl border border-white/60">
              <div className="border-b border-slate-100 bg-slate-50 px-6 py-5 sm:px-8">
                <div className="mb-2 flex items-center gap-2 text-xs font-black text-orange-600"><Sparkles className="h-4 w-4" /> 主控体验配置</div>
                <h3 className="text-2xl font-extrabold text-slate-900">这趟旅程，你最期待什么？</h3>
                <p className="mt-1 text-xs sm:text-sm text-slate-500 font-medium">选择一个主偏好，进入沙盘后仍可随时调整，不会锁死行程。</p>
              </div>
              <div className="grid grid-cols-1 gap-3 px-6 py-5 sm:grid-cols-2 sm:px-8">
                <PreferenceCard id="foodie" icon={<Coffee/>} title="寻味探索" desc="美食驱动，为您匹配地道餐馆。" selected={selectedRole === '寻味探索'} onClick={() => setSelectedRole('寻味探索')} color="border-orange-500 bg-orange-50/80 text-orange-600" />
                <PreferenceCard id="photo" icon={<Camera/>} title="视觉体验" desc="出片导向，精准匹配最佳摄影光线。" selected={selectedRole === '视觉体验'} onClick={() => setSelectedRole('视觉体验')} color="border-purple-500 bg-purple-50/80 text-purple-600" />
                <PreferenceCard id="chill" icon={<Car/>} title="休闲漫步" desc="拒绝特种兵，安排宽裕休息漫游。" selected={selectedRole === '休闲漫步'} onClick={() => setSelectedRole('休闲漫步')} color="border-blue-500 bg-blue-50/80 text-blue-600" />
                <PreferenceCard id="hardcore" icon={<MapIcon/>} title="深度探索" desc="行程紧凑，打卡最多核心文旅地标。" selected={selectedRole === '深度探索'} onClick={() => setSelectedRole('深度探索')} color="border-emerald-500 bg-emerald-50/80 text-emerald-600" />
              </div>
              <div className="mx-6 mb-5 flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold sm:mx-8">
                <span className={`h-2 w-2 rounded-full ${roomReady ? 'bg-emerald-500' : 'bg-amber-500 animate-pulse'}`} />
                <span className={roomReady ? 'text-emerald-700' : 'text-amber-700'}>{roomReady ? '协作房间已就绪，可与同行实时同步' : '协作房间正在连接；可先进入填写需求'}</span>
              </div>
              <div className="flex gap-3 border-t border-slate-100 bg-white px-6 py-4 sm:px-8">
                <button onClick={() => !isConnecting && setShowPreferenceModal(false)} className="flex-1 py-3 bg-slate-50 border border-slate-200 text-slate-600 font-bold rounded-xl hover:bg-slate-100 transition-colors cursor-pointer text-sm">返回调整</button>
                <button onClick={handleConfirmSync} disabled={!selectedRole || isConnecting} className={`flex-[1.5] py-3 rounded-xl font-bold flex items-center justify-center gap-2 transition-all cursor-pointer text-sm ${!selectedRole ? 'bg-slate-100 text-slate-400' : isConnecting ? 'bg-orange-500 text-white' : 'bg-orange-500 text-white hover:bg-orange-600 shadow-md shadow-orange-200/50'}`}>
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

      {/* ================= 交通票务查询 Modal ================= */}
      <AnimatePresence>
        {showTransportSearch && (
          <motion.div
            key="transport-search-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[150] bg-slate-950/60 backdrop-blur-sm"
            onClick={() => setShowTransportSearch(false)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="absolute inset-4 sm:inset-8 md:inset-16 bg-white dark:bg-slate-800 rounded-2xl shadow-2xl overflow-hidden"
              onClick={(e) => e.stopPropagation()}
            >
              <TransportSearch onClose={() => setShowTransportSearch(false)} />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <BookingHub
        open={showBookingHub}
        destination={planDestination || ''}
        onClose={() => setShowBookingHub(false)}
      />
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
  // Use the live room as the resource key while drafting, then switch to the
  // persisted trip ID after saving. City names are display data, not IDs.
  const [savedTripId, setSavedTripId] = useState('');
  const planningResourceId = savedTripId || roomCode;

  // 👑 交互页内可实时切换的出行方式 / 偏好定位（与后端 user_preferences.mode / role 对齐）
  const [activeMode, setActiveMode] = useState<string>(mode);
  const [activeRole, setActiveRole] = useState<string>(role);
  
  const [selectedPoiIndex, setSelectedPoiIndex] = useState<number | null>(0);
  const [activeDayIndex, setActiveDayIndex] = useState<number>(0);

  const [userIntent, setUserIntent] = useState(initialDestination ? `去${initialDestination}玩3天` : '');
  const [dynamicRoutes, setDynamicRoutes] = useState<any[]>([]);
  const [replanProposal, setReplanProposal] = useState<any | null>(null);
  const [replanBusy, setReplanBusy] = useState(false);
  const [realPath, setRealPath] = useState<[number, number][]>([]);
  const [targetCityInfo, setTargetCityInfo] = useState<{name: string, lnglat: [number, number]} | null>(null);

  const [weatherInfo, setWeatherInfo] = useState<any>(null);
  const [trafficInfo, setTrafficInfo] = useState<any>(null);
  const [safetyInfo, setSafetyInfo] = useState<any>(null);
  const [safetyBrief, setSafetyBrief] = useState<string>('');
  const [briefLoading, setBriefLoading] = useState(false);
  const [showPoster, setShowPoster] = useState(false);
  const [showDiary, setShowDiary] = useState(false);
  const [showBudget, setShowBudget] = useState(false);
  const [showExpenseReport, setShowExpenseReport] = useState(false);
  const [showSatisfaction, setShowSatisfaction] = useState(false);
  const [showBookingHub, setShowBookingHub] = useState(false);
  const [photoModalOpen, setPhotoModalOpen] = useState(false);
  const [photoModalPoi, setPhotoModalPoi] = useState<{name: string; type?: string; city?: string} | null>(null);
  const [activePhotoSource, setActivePhotoSource] = useState(0);
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
  const [visitedNodes, setVisitedNodes] = useState<string[]>([]);
  const [feedbackPoi, setFeedbackPoi] = useState<string | null>(null);
  const [feedbackReason, setFeedbackReason] = useState('');

  const wsRef = useRef<WebSocket | null>(null);
  const autoSavedRef = useRef(false);
  const lastTokenTimeRef = useRef<number>(Date.now());
  const [wsConnected, setWsConnected] = useState(false);
  const [deductionTimeout, setDeductionTimeout] = useState(false);
  const [deductionError, setDeductionError] = useState<string | null>(null);
  const [isFallbackRoute, setIsFallbackRoute] = useState(false);
  // 👑 多方案：后端返回的多套行程方案与当前选中方案
  const [planVariants, setPlanVariants] = useState<any[]>([]);
  const [activePlanId, setActivePlanId] = useState<string>('primary');
  const [planSwitchNotice, setPlanSwitchNotice] = useState('');
  // The bandit arm the planner used for this plan, plus why it chose it. The arm
  // is sent back with the satisfaction survey so the reward can be attributed;
  // without it the policy has nothing to learn from.
  const [banditArmId, setBanditArmId] = useState<string>('');
  const [banditDecision, setBanditDecision] = useState<{ reason?: string; explored?: boolean; propensity?: number } | null>(null);

  const planQuality = useMemo(() => {
    const nodes = Array.isArray(dynamicRoutes) ? dynamicRoutes : [];
    const days = new Set(nodes.map((node: any) => Number(node.day) || 1));
    const dailyCounts = Array.from(days).sort((a, b) => a - b).map((day) => ({ day, count: nodes.filter((node: any) => (Number(node.day) || 1) === day && !node.is_hotel && !node.tags?.includes('住宿')).length }));

    // Cost parsing must distinguish three different situations that used to
    // collapse into one: a node with a real price, a node explicitly priced at
    // zero (free entry), and a node with no supplier price at all. The old
    // implementation matched /[\d,.]+/ against the sentinel string
    // "暂无供应商数据" — which yields no match — and then defaulted to 0, so a
    // completely unpriced itinerary advertised a total of ¥0. Unpriced nodes are
    // now excluded from the sum and counted separately so the UI can say so.
    let pricedSum = 0;
    let unpricedCount = 0;
    for (const node of nodes) {
      const raw = node?.cost;
      if (raw === null || raw === undefined || typeof raw === 'boolean') {
        unpricedCount++;
        continue;
      }
      if (typeof raw === 'number') {
        if (Number.isFinite(raw)) pricedSum += raw;
        else unpricedCount++;
        continue;
      }
      const text = String(raw).trim();
      // Require a currency signal so a year-like value ("2026年") is not read
      // as a price.
      const match = /(?:[¥￥]\s*([\d,]+(?:\.\d+)?))|(?:([\d,]+(?:\.\d+)?)\s*(?:元|块|人民币))/.exec(text);
      if (!match) {
        unpricedCount++;
        continue;
      }
      const value = Number((match[1] || match[2] || '').replace(/,/g, ''));
      if (Number.isFinite(value)) pricedSum += value;
      else unpricedCount++;
    }
    const pricedCount = nodes.length - unpricedCount;
    const estimatedCost = Math.round(pricedSum * 100) / 100;

    const verified = nodes.filter((node: any) => Array.isArray(node.lnglat) && node.lnglat.length >= 2).length;
    const votes = Object.values(nodeVotes);
    const positiveVotes = votes.filter((value) => value === 'up').length;
    const voteCoverage = nodes.length ? Math.min(1, votes.length / nodes.length) : 0;
    const memberBaseline = roomMembers.length ? Math.min(1, roomMembers.length / 4) : 0.25;
    const consensus = nodes.length
      ? Math.round(((positiveVotes / Math.max(1, votes.length)) * 0.55 + voteCoverage * 0.2 + memberBaseline * 0.25) * 100)
      : 0;
    return {
      nodes: nodes.length,
      days: days.size,
      estimatedCost,
      pricedCount,
      unpricedCount,
      // True when the itinerary exists but carries no usable price anywhere.
      costUnavailable: nodes.length > 0 && pricedCount === 0,
      coverage: nodes.length ? Math.round((verified / nodes.length) * 100) : 0,
      consensus,
      dailyCounts,
    };
  }, [dynamicRoutes, nodeVotes, roomMembers]);

  // 👑 将后端路线节点规范化为前端展示结构（与 final_route 映射保持一致）
  const mapRouteForDynamic = (fr: any[]) => (Array.isArray(fr) ? fr : []).map((r: any) => ({
    day: r.day || 1,
    name: r.location || r.name,
    lnglat: normalizeLnglat(r.lnglat),
    coordinate_status: normalizeLnglat(r.lnglat) ? 'verified' : 'missing',
    color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6",
    desc: r.desc || r.action,
    time: r.time,
    time_reason: r.time_reason || "",
    transport: r.transport,
    tags: r.tags || [],
    cost: r.cost_estimate || r.cost || "暂无供应商数据",
    photos: r.photos || [],
    trust_reason: r.trust_reason || "核心地标推荐",
    amap_url: r.amap_url || "",
    map_image: r.map_image || "",
    rating: r.rating || "暂无供应商数据",
    open_time: r.open_time || "暂无供应商数据",
    address: r.address || "",
    hotel_candidates: r.hotel_candidates || [],
    split_info: r.split_info || "",
    merge_point: Boolean(r.merge_point),
    is_hotel: Boolean(r.is_hotel || r.tags?.includes("住宿")),
    riskScore: r.risk_score ?? 0,
    crowdedness: r.crowdedness || "",
  }));

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
      ws = new WebSocket(`${WS_BASE}/ws?room_id=${encodeURIComponent(roomCode || 'OMNI-88')}&access_token=${encodeURIComponent(getAuthToken())}&user_id=${encodeURIComponent(currentUser.id)}`);
      
      ws.onopen = () => {
        setApiError(null);
        setWsConnected(true);
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
              lastTokenTimeRef.current = Date.now();
              setDeductionTimeout(false);
              setDeductionError(null);
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
                    lnglat: normalizeLnglat(r.lnglat),
                    coordinate_status: normalizeLnglat(r.lnglat) ? 'verified' : 'missing',
                    color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6", 
                    desc: r.desc || r.action,
                    time: r.time,             
                    time_reason: r.time_reason || "",
                    transport: r.transport,   
                    tags: r.tags || [],             
                    cost: r.cost_estimate || r.cost || "暂无供应商数据",
                    photos: r.photos || [],
                    trust_reason: r.trust_reason || "核心地标推荐",
                    amap_url: r.amap_url || "",
                    map_image: r.map_image || "",
                    rating: r.rating || "暂无供应商数据",
                    open_time: r.open_time || "暂无供应商数据",
                    address: r.address || "",
                    hotel_candidates: r.hotel_candidates || [],
                    split_info: r.split_info || "",
                    merge_point: Boolean(r.merge_point),
                    is_hotel: Boolean(r.is_hotel || r.tags?.includes("住宿")),
                    riskScore: r.risk_score ?? 0,
                    crowdedness: r.crowdedness || "",
                  }));
                  
                  setDynamicRoutes(mappedRoutes);
                  if (finalData.negotiation_summary) setConsensusSummary(finalData.negotiation_summary);
                  if (finalData.team_satisfaction) setTeamSatisfaction(finalData.team_satisfaction);
                  if (finalData.arbitration_records) setArbitrationRecords(finalData.arbitration_records);
                  // Capture the bandit arm the planner actually used. Without
                  // this the satisfaction form posts an empty bandit_arm_id, the
                  // gateway drops the reward, and the policy accumulates no
                  // evidence at all — the learning loop was broken here.
                  if (finalData.bandit && typeof finalData.bandit === 'object') {
                    setBanditArmId(
                      typeof finalData.bandit.arm_id === 'string' ? finalData.bandit.arm_id : '',
                    );
                    setBanditDecision({
                      reason: finalData.bandit.reason,
                      explored: finalData.bandit.explored,
                      propensity: finalData.bandit.propensity,
                    });
                  } else {
                    // The bandit is disabled by configuration: clear any stale
                    // arm so we never attribute a reward to a previous run.
                    setBanditArmId('');
                    setBanditDecision(null);
                  }
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
              // 推演阶段错误：不跳回 drafting，让 DeductionPanel 展示错误+重试按钮，
              // 同时等待后端可能下发的保底 final_route
              const errMsg = msg.payload || "推演服务异常";
              setApiError(errMsg);
              setDeductionError(errMsg);
            } else if (msg.type === "pvp_price") {
              setPvpPriceIntel(msg.payload);
              setPvpPriceLoading(false);
            } else if (msg.type === "actual_path") {
              setRealPath(Array.isArray(msg.payload) ? msg.payload : []);
            } else if (msg.type === "final_route") {
              const fd = msg.payload || {};
              const fr = Array.isArray(fd.route) ? fd.route : [];
              // 清除推演阶段的错误/超时状态
              setDeductionError(null);
              setDeductionTimeout(false);
              setIsFallbackRoute(fd.status === "degraded_fallback");
              if (fr.length > 0) {
                const mapped = fr.map((r: any) => ({
                  day: r.day || 1,
                  name: r.location || r.name,
                  lnglat: normalizeLnglat(r.lnglat),
                  coordinate_status: normalizeLnglat(r.lnglat) ? 'verified' : 'missing',
                  color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6",
                  desc: r.desc || r.action,
                  time: r.time,
                  time_reason: r.time_reason || "",
                  transport: r.transport,
                  tags: r.tags || [],
                  cost: r.cost_estimate || r.cost || "暂无供应商数据",
                  photos: r.photos || [],
                  trust_reason: r.trust_reason || "核心地标推荐",
                  amap_url: r.amap_url || "",
                  map_image: r.map_image || "",
                  rating: r.rating || "暂无供应商数据",
                  open_time: r.open_time || "暂无供应商数据",
                  address: r.address || "",
                  hotel_candidates: r.hotel_candidates || [],
                  split_info: r.split_info || "",
                  merge_point: Boolean(r.merge_point),
                  is_hotel: Boolean(r.is_hotel || r.tags?.includes("住宿")),
                  riskScore: r.risk_score ?? 0,
                  crowdedness: r.crowdedness || "",
                }));
                setDynamicRoutes(mapped);
                setSelectedPoiIndex(0);
                setActiveDayIndex(0);
                setPhase('decision');
                // 👑 多方案：主方案 + 后端返回的差异化方案，一并存入供切换
                const _vs = Array.isArray(fd.plan_variants)
                  ? fd.plan_variants.map((v: any) => ({ id: v.id, name: v.name, style: v.style, desc: v.desc, nodes: mapRouteForDynamic(v.route) })).filter((v: any) => Array.isArray(v.nodes) && v.nodes.length > 0)
                  : [];
                setPlanVariants([{ id: 'primary', name: 'AI 主方案', style: 'primary', desc: '', nodes: mapped }, ..._vs]);
                setActivePlanId('primary');
                setPlanSwitchNotice('');
              }
              if (fd.negotiation_summary) setConsensusSummary(fd.negotiation_summary);
              if (fd.team_satisfaction) setTeamSatisfaction(fd.team_satisfaction);
              if (fd.arbitration_records) setArbitrationRecords(fd.arbitration_records);
            }
          }
        } catch (err) {}
      };

      ws.onerror = () => { setApiError("协同网络中断，请检查后端网关是否正常运行。"); setWsConnected(false); };
      ws.onclose = () => {
        if (!isMounted) return;
        setWsConnected(false);
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

  // 👑 客户端超时保护：deduction 阶段超过 90s 未收到任何 token 则提示重试
  useEffect(() => {
    if (phase !== 'deduction') return;
    const timer = setInterval(() => {
      const elapsed = Date.now() - lastTokenTimeRef.current;
      if (elapsed > 90_000) {
        setDeductionTimeout(true);
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [phase]);

  // Route delivery completes the reasoning step; return focus to the map so
  // the next actions are immediately available without a blocking drawer.
  useEffect(() => {
    if (phase === 'decision') setShowBlackboard(false);
  }, [phase]);

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

  const handleSubmitIntent = (intentOverride?: unknown) => {
    const intent = typeof intentOverride === 'string' ? intentOverride : userIntent;
    if (!intent.trim()) {
      setApiError('请先描述目的地、天数或本次旅行的重点诉求');
      return;
    }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setApiError('协同网络正在连接，请稍候再点击“开始推演”');
      return;
    }

    setIntentsReady(true);
    setPhase('deduction');
    setShowBlackboard(true); 
    setApiError(null);
    setStreamedText(""); 
    setDeductionError(null);
    setDeductionTimeout(false);
    setIsFallbackRoute(false);
    lastTokenTimeRef.current = Date.now();
    autoSavedRef.current = false;

    const updatedHistory = [...incrementalHistory, intent];
    setIncrementalHistory(updatedHistory);

    // 动态同步发送当前房间全部成员画像
    const wsPayload = {
      type: "agent_negotiate",
      payload: {
        destinations: [],
        user_preferences: {
          mode: activeMode,
          role: activeRole, 
          intent,
          history_sequence: updatedHistory,
          current_existing_route: dynamicRoutes,
          room_members: roomMembers.map((m: any) => ({
            id: m.id,
            name: m.name,
            role: m.role,
            intent: m.intent,
            avatarUrl: m.avatarUrl || m.avatar_url || ''
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
    setDeductionError(null);
    setDeductionTimeout(false);
    setIsFallbackRoute(false);
    setStreamedText("");
  };

  // 推演失败/超时后重试：重置状态并重新发送 agent_negotiate
  const handleRetryDeduction = () => {
    if (!wsRef.current) {
      setApiError("连接已断开，请刷新页面重试");
      return;
    }
    setDeductionError(null);
    setDeductionTimeout(false);
    setStreamedText("");
    lastTokenTimeRef.current = Date.now();
    const payload = {
      type: "agent_negotiate",
      payload: {
        destinations: [],
        user_preferences: {
          mode: activeMode,
          role: activeRole,
          intent: userIntent,
          history_sequence: incrementalHistory,
          current_existing_route: dynamicRoutes,
          room_members: roomMembers.map((m: any) => ({
            id: m.id, name: m.name, role: m.role, intent: m.intent, avatarUrl: m.avatarUrl || m.avatar_url || ''
          }))
        }
      }
    };
    try {
      if (wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(payload));
      } else {
        // 连接未就绪，等待自动重连后提示用户再点
        setApiError("连接正在恢复中，请 2 秒后再次点击重试");
      }
    } catch {
      setApiError("重试失败，请刷新页面后重试");
    }
  };

  const persistTripRoutes = async (routesToSave: any[]) => {
    if (!routesToSave || routesToSave.length === 0) return false;
    const cityName = targetCityInfo?.name || '';
    const title = `${cityName || '我的行程'}${totalDays.length}日行程`;
    const content = JSON.stringify({
      summary: consensusSummary,
      city: cityName,
      days: totalDays,
      routes: routesToSave
    });
    try {
      const res = await fetch(`${API_BASE}/api/user/trip`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, title, dest_city: cityName, content, days: totalDays.length, travelers: roomMembers.length > 0 ? roomMembers.length : 1 })
      });
      const rawText = await res.text();
      let data: any = null;
      try { data = JSON.parse(rawText); } catch { throw new Error(`行程响应异常 (HTTP ${res.status})`); }
      if (!res.ok) throw new Error(data?.error || '行程保存失败');
      if (typeof data?.trip_id === 'string' && data.trip_id.trim()) {
        setSavedTripId(data.trip_id.trim());
      }
      setTripSaved(true);
      setTimeout(() => setTripSaved(false), 1800);
      return true;
    } catch (e: any) {
      setApiError(e.message || '行程保存失败');
      return false;
    }
  };

  const handleSaveTrip = async () => {
    await persistTripRoutes(dynamicRoutes);
  };

  const applyReplanCandidate = async (candidate: any) => {
    if (!replanProposal || replanBusy) return;
    setReplanBusy(true);
    try {
      const fairness = replanProposal.data?.fairness;
      if (fairness?.status === 'evaluated' && fairness.threshold_exceeded === true) {
        throw new Error('成员公平指标超过阈值，当前提案不能直接应用');
      }
      const affected = Array.isArray(replanProposal.action?.affected_nodes) ? replanProposal.action.affected_nodes : [];
      if (affected.length !== 1) throw new Error('当前提案必须拆分为逐节点替换后才能应用');
      const targetIndex = dynamicRoutes.findIndex((node: any) => affected.includes(String(node?.name || node?.location || '').trim()));
      if (targetIndex < 0) throw new Error('无法定位待替换节点，提案未应用');
      const target = dynamicRoutes[targetIndex];
      const targetKey = `${Number(target?.day) || 1}:${String(target?.name || target?.location || '').trim()}`;
      const executionResponse = await apiFetch(`${API_BASE}/api/v1/planning/execution?trip_id=${encodeURIComponent(planningResourceId)}`);
      const executionPayload = await executionResponse.json();
      const executionRows = executionPayload?.data?.states || executionPayload?.states || [];
      const executionState = Array.isArray(executionRows) ? executionRows.find((row: any) => row?.node_key === targetKey) : null;
      if (executionState && ['visited', 'skipped'].includes(String(executionState.status))) {
        throw new Error(`节点“${target.name || target.location}”已经${executionState.status === 'visited' ? '到访' : '跳过'}，不能再替换`);
      }
      const validationResponse = await apiFetch(`${API_BASE}/api/v1/planning/replan/validate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          trip_id: planningResourceId,
          destination: targetCityInfo?.name || '',
          existing_route: dynamicRoutes,
          affected_nodes: affected,
          candidate,
        }),
      });
      const validationPayload = await validationResponse.json();
      const validation = validationPayload?.data || validationPayload;
      if (!validationResponse.ok || validation?.hard_satisfied !== true) {
        const reasons = Array.isArray(validation?.violations) ? validation.violations.join('、') : '硬约束未通过';
        throw new Error(`候选未通过应用校验：${reasons}`);
      }
      const nextRoutes = [...dynamicRoutes];
      nextRoutes[targetIndex] = {
        ...target,
        ...candidate,
        name: candidate.name || candidate.location || target.name,
        location: candidate.location || candidate.name || target.location,
        lnglat: candidate.lnglat || candidate.coordinate || target.lnglat,
        coordinate_status: candidate.lnglat || candidate.coordinate ? 'verified' : target.coordinate_status,
        source: candidate.source || target.source,
        replacement_reason: replanProposal.action?.reason || '应急重规划替换',
      };
      setDynamicRoutes(nextRoutes);
      const saved = await persistTripRoutes(nextRoutes);
      if (!saved) throw new Error('替代节点已在当前页面更新，但行程保存失败');
      await apiFetch(`${API_BASE}/api/v1/planning/events`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: planningResourceId, event_type: 'replan_applied', payload: { proposal_id: replanProposal.proposalId || '', action_id: replanProposal.action?.id || '', candidate_id: candidate.id || '', candidate_name: candidate.name || candidate.location || '' } }),
      });
      setReplanProposal(null);
      setApiError('应急替代节点已应用并保存，未受影响节点保持不变');
    } catch (error) {
      setApiError(error instanceof Error ? error.message : '应用应急提案失败');
    } finally {
      setReplanBusy(false);
    }
  };

  const rejectReplanProposal = async () => {
    if (!replanProposal || replanBusy) return;
    setReplanBusy(true);
    try {
      await apiFetch(`${API_BASE}/api/v1/planning/events`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: planningResourceId, event_type: 'replan_proposal_rejected', payload: { proposal_id: replanProposal.proposalId || '', action_id: replanProposal.action?.id || '' } }),
      });
      setReplanProposal(null);
      setApiError('已拒绝应急重规划提案，当前行程保持不变');
    } catch (error) {
      setApiError(error instanceof Error ? error.message : '记录提案拒绝失败');
    } finally {
      setReplanBusy(false);
    }
  };

  const downloadTextFile = (filename: string, content: string, mime: string) => {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  };
  const diaryMarkdown = buildDiaryMarkdown(dynamicRoutes, { city: targetCityInfo?.name || '' });
  const downloadIcs = () => downloadTextFile(`${targetCityInfo?.name || '行程'}_行程日历.ics`, buildIcsCalendar(dynamicRoutes, { city: targetCityInfo?.name || '' }), 'text/calendar;charset=utf-8');
  const downloadDiary = () => downloadTextFile(`${targetCityInfo?.name || '行程'}_旅行日记.md`, diaryMarkdown, 'text/markdown;charset=utf-8');
  const downloadJson = () => downloadTextFile(`${targetCityInfo?.name || '行程'}_路书.json`, JSON.stringify({ city: targetCityInfo?.name || '', routes: dynamicRoutes }, null, 2), 'application/json;charset=utf-8');

  // 👑 自动保存到“我的路书”：首次生成完整路线后静默落库，无需手动点击
  useEffect(() => {
    if (phase === 'decision' && dynamicRoutes.length > 0 && !autoSavedRef.current) {
      autoSavedRef.current = true;
      handleSaveTrip();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, dynamicRoutes]);

  const [secondaryPoi, setSecondaryPoi] = useState<any | null>(null);
  const handleSinglePoiSwap = (poiName: string) => {
    // Never substitute a placeholder city. The previous fallback silently
    // rewrote the user's request to a hardcoded destination, so a swap made
    // before the workspace had a resolved city would ask the planner to work
    // on the wrong place. Ask the user to state the city instead.
    const currentCityName = targetCityInfo?.name;
    if (!currentCityName) {
      setApiError('还没有确定目的地城市，请先描述目的地或完成一次推演后再替换景点');
      return;
    }
    void fetch(`${API_BASE}/api/v1/planning/events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trip_id: planningResourceId, event_type: 'plan_swapped', payload: { module: 'route', target: poiName, reason: '用户替换该景点' } }),
    }).catch(() => {});
    const swapPrompt = `保持在【${currentCityName}】不变，仅把行程中的景点【${poiName}】平替换成【${currentCityName}】另一个同等知名度的高评分文旅景点或特色餐厅，绝对不要改变城市和其他天数的规划。`;
    setUserIntent(swapPrompt);
    handleSubmitIntent(swapPrompt);
  };

  const handleNodeVisited = (poi: any) => {
    const key = `${poi?.day || 1}:${poi?.name || ''}`;
    setVisitedNodes((previous) => previous.includes(key) ? previous : [...previous, key]);
    void apiFetch(`${API_BASE}/api/v1/planning/execution`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trip_id: planningResourceId, plan_id: savedTripId || undefined, node_key: key, status: 'visited', note: `第${poi?.day || 1}天 · ${poi?.name || ''}` }),
    }).catch(() => {});
  };

  const handleVote = (poiName: string, type: 'up' | 'down') => {
    const nextValue = nodeVotes[poiName] === type ? null : type;
    setNodeVotes(prev => ({
      ...prev,
      [poiName]: nextValue as any
    }));
    // 上报正/负反馈到进化闭环，形成系统记忆
    if (type === 'down') {
      setFeedbackPoi(poiName);
      setFeedbackReason('');
      return;
    }
    fetch(`${API_BASE}/api/v1/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_id: roomCode, user_id: currentUser.id, target: poiName, score: type === 'up' ? 1 : -1, reason: '用户对路线节点评价' })
    }).catch(() => {});
  };

  const submitPoiFeedback = () => {
    if (!feedbackPoi) return;
    void fetch(`${API_BASE}/api/v1/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_id: roomCode, user_id: currentUser.id, target: feedbackPoi, score: -1, reason: feedbackReason || '用户不喜欢该地点' }),
    }).catch(() => {});
    setFeedbackPoi(null);
    setFeedbackReason('');
  };

  // 👑 A/B 方案：换一个调性，生成与当前版本不同的 B 方案
  const handleRegenerateVariant = () => {
    // Same reasoning as the single-POI swap: a missing city must be surfaced,
    // not replaced by a hardcoded destination.
    const currentCityName = targetCityInfo?.name;
    if (!currentCityName) {
      setApiError('还没有确定目的地城市，请先完成一次推演后再生成 B 方案');
      return;
    }
    const variantPrompt = `请给【${currentCityName}】规划一个与当前方案不同调性的 B 版本：换一种行程节奏与景点组合（例如更松弛 vs 更紧凑、或更偏爱小众 vs 更主打地标打卡），保持城市与天数不变，重新推演一版全新路线。`;
    setUserIntent(variantPrompt);
    handleSubmitIntent(variantPrompt);
  };

  // 👑 切换后端生成的多套方案（点击即本地即时生效，无需重新推演）
  const handleSelectPlan = (id: string) => {
    const v = planVariants.find((x: any) => x.id === id);
    if (v && Array.isArray(v.nodes) && v.nodes.length > 0) {
      setDynamicRoutes(v.nodes);
      setActivePlanId(id);
      setActiveDayIndex(0);
      setSelectedPoiIndex(0);
      setPlanSwitchNotice(`已切换至「${v.name || '新方案'}」：${v.desc || '路线节点与节奏已更新'}`);
    }
  };

  // 👑 AI 安全简报生成
  const generateSafetyBrief = async () => {
    if (!safetyInfo || !targetCityInfo) return;
    setBriefLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/briefing/safety`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          city: targetCityInfo.name,
          cii_score: safetyInfo.cii_score,
          risk_level: safetyInfo.risk_level,
          active_alerts: safetyInfo.active_alerts,
          weather: weatherInfo,
          traffic: trafficInfo,
        }),
      });
      const data = await res.json();
      setSafetyBrief(data.briefing || '无法生成安全简报');
    } catch (e) {
      setSafetyBrief('安全简报服务暂时不可用');
    }
    setBriefLoading(false);
  };

  return (
    <motion.div key="workspace-root" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} className="h-screen w-full bg-white flex flex-col lg:flex-row overflow-y-auto lg:overflow-hidden font-sans text-slate-800">
      
      {/* 左侧长图文游记与路径排版区 */}
      <div className="w-full lg:w-[620px] xl:w-[700px] h-[56vh] lg:h-full min-h-[480px] lg:min-h-0 flex flex-col relative z-20 border-b lg:border-b-0 lg:border-r border-slate-200 bg-white shadow-2xl shrink-0">
        <header className="px-5 sm:px-8 py-4 border-b border-slate-100 bg-white shadow-xs z-10">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-2xl font-black text-slate-800 tracking-tight">{targetCityInfo ? targetCityInfo.name + '全景路线规划' : '规划您的行程'}</h2>
            <p className="text-xs text-slate-400 font-bold mt-0.5">OmniRoute 多智能体联网知识增强交付</p>
          </div>
          {phase === 'decision' && (
            <div className="workspace-toolbar grid w-full grid-cols-2 gap-2 sm:w-[430px] sm:grid-cols-3" aria-label="行程工具">
              <button data-testid="save-trip" onClick={handleSaveTrip} className="px-3.5 py-1.5 bg-emerald-50 text-emerald-600 rounded-xl font-bold text-xs hover:bg-emerald-100 transition-colors flex items-center gap-1.5 border border-emerald-100 shadow-2xs cursor-pointer">
                {tripSaved ? <Check className="w-3.5 h-3.5" /> : <Save className="w-3.5 h-3.5" />} {tripSaved ? '已保存' : '保存到我的行程'}
              </button>
              <button onClick={handleRegenerateVariant} className="px-3.5 py-1.5 bg-blue-50 text-blue-600 rounded-xl font-bold text-xs hover:bg-blue-100 transition-colors flex items-center gap-1.5 border border-blue-100 shadow-2xs cursor-pointer">
                <Shuffle className="w-3.5 h-3.5"/> 换一版
              </button>
              <button onClick={() => setShowPoster(true)} className="px-3.5 py-1.5 bg-purple-50 text-purple-600 rounded-xl font-bold text-xs hover:bg-purple-100 transition-colors flex items-center gap-1.5 border border-purple-100 shadow-2xs cursor-pointer">
                <Share2 className="w-3.5 h-3.5"/> 行程海报
              </button>
              <button data-testid="open-diary" onClick={() => setShowDiary(true)} className="px-3.5 py-1.5 bg-amber-50 text-amber-600 rounded-xl font-bold text-xs hover:bg-amber-100 transition-colors flex items-center gap-1.5 border border-amber-100 shadow-2xs cursor-pointer"><BookOpen className="w-3.5 h-3.5"/> AI 日记</button>
              <button data-testid="export-ics" onClick={downloadIcs} className="px-3.5 py-1.5 bg-teal-50 text-teal-600 rounded-xl font-bold text-xs hover:bg-teal-100 transition-colors flex items-center gap-1.5 border border-teal-100 shadow-2xs cursor-pointer"><Download className="w-3.5 h-3.5"/> 导出日历</button>
              <button data-testid="export-json" onClick={downloadJson} className="px-3.5 py-1.5 bg-slate-50 text-slate-600 rounded-xl font-bold text-xs hover:bg-slate-100 transition-colors flex items-center gap-1.5 border border-slate-200 shadow-2xs cursor-pointer"><Download className="w-3.5 h-3.5"/> 导出 JSON</button>
              <button onClick={() => setShowBudget(true)} className="px-3.5 py-1.5 bg-cyan-50 text-cyan-700 rounded-xl font-bold text-xs hover:bg-cyan-100 transition-colors flex items-center gap-1.5 border border-cyan-100 shadow-2xs cursor-pointer" title="打开预算管家"><PiggyBank className="w-3.5 h-3.5"/> 预算账本</button>
              <button onClick={() => setShowExpenseReport(true)} className="px-3.5 py-1.5 bg-indigo-50 text-indigo-700 rounded-xl font-bold text-xs hover:bg-indigo-100 transition-colors flex items-center gap-1.5 border border-indigo-100 shadow-2xs cursor-pointer" title="查看消费复盘"><TrendingDown className="w-3.5 h-3.5"/> 消费复盘</button>
              <button onClick={() => setShowSatisfaction(true)} className="px-3.5 py-1.5 bg-rose-50 text-rose-700 rounded-xl font-bold text-xs hover:bg-rose-100 transition-colors flex items-center gap-1.5 border border-rose-100 shadow-2xs cursor-pointer" title="提交行程满意度"><Star className="w-3.5 h-3.5"/> 行程评价</button>
              <button onClick={() => setShowBookingHub(true)} className="px-3.5 py-1.5 bg-orange-50 text-orange-700 rounded-xl font-bold text-xs hover:bg-orange-100 transition-colors flex items-center gap-1.5 border border-orange-100 shadow-2xs cursor-pointer" title="打开官方渠道"><ExternalLink className="w-3.5 h-3.5"/> 官方渠道</button>
              <button onClick={() => { setPhase('drafting'); setIntentsReady(false); }} className="px-3.5 py-1.5 bg-orange-50 text-orange-600 rounded-xl font-bold text-xs hover:bg-orange-100 transition-colors flex items-center gap-1.5 border border-orange-100 shadow-2xs cursor-pointer">
                <Wand2 className="w-3.5 h-3.5"/> 重新调整
              </button>
            </div>
          )}
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2" aria-label="规划进度">
            {([
              ['drafting', '01', '描述需求'],
              ['deduction', '02', '多智能体推演'],
              ['decision', '03', '共识路书'],
            ] as const).map(([key, index, label]) => {
              const active = phase === key;
              const complete = (key === 'drafting' && phase !== 'drafting') || (key === 'deduction' && phase === 'decision');
              return <div key={key} className={`relative flex items-center gap-2 rounded-xl px-3 py-2 border transition-colors ${active ? 'bg-orange-50 border-orange-200 text-orange-700' : complete ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-slate-50 border-slate-100 text-slate-400'}`}>
                <span className="font-mono text-[10px] font-black">{complete ? '✓' : index}</span><span className="text-[11px] font-bold truncate">{label}</span>
              </div>;
            })}
          </div>
          {phase === 'decision' && (
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-bold text-slate-500" aria-label="路书质量摘要">
              <span><strong className="text-slate-800">{planQuality.days}</strong> 天 · <strong className="text-slate-800">{planQuality.nodes}</strong> 个节点</span>
              {planQuality.costUnavailable ? (
                <span className="text-amber-600" title="供应商未返回价格，无法给出估算总额">
                  价格待补充<strong className="ml-1">（{planQuality.unpricedCount} 个节点无报价）</strong>
                </span>
              ) : (
                <span>
                  估算 ¥<strong className="text-orange-600">{planQuality.estimatedCost.toLocaleString()}</strong>
                  {planQuality.unpricedCount > 0 && (
                    <span className="ml-1 font-medium text-slate-400" title="这些节点缺少供应商报价，未计入合计">
                      · {planQuality.unpricedCount} 个未计价
                    </span>
                  )}
                </span>
              )}
              <span>坐标覆盖 <strong className={planQuality.coverage >= 80 ? 'text-emerald-600' : 'text-amber-600'}>{planQuality.coverage}%</strong></span>
              <span className={planQuality.consensus >= 70 ? 'text-emerald-600' : 'text-amber-600'}>共识指数 <strong>{planQuality.consensus}</strong></span>
              {/* Make the learning step visible: which style the policy picked,
                  and whether it was exploring or exploiting. Without this the
                  bandit's decisions were invisible in the product. */}
              {banditDecision && (
                <span
                  className={banditDecision.explored ? 'text-violet-600' : 'text-sky-600'}
                  title={
                    banditDecision.propensity !== undefined
                      ? `该方案被选中的概率约 ${(banditDecision.propensity * 100).toFixed(1)}%`
                      : '策略选择'
                  }
                >
                  {banditDecision.explored ? '策略探索' : '策略择优'}
                  <strong className="ml-1">{banditArmId ? banditArmId.split(':').slice(-1)[0] : '—'}</strong>
                </span>
              )}
              <span className="inline-flex items-center gap-1 text-emerald-600"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />团队实时同步</span>
              {planQuality.dailyCounts.length > 0 && <span className="text-slate-400">白天节点 {planQuality.dailyCounts.map((item: any) => `D${item.day}:${item.count}`).join(' · ')}</span>}
            </div>
          )}
        </header>

        <div className="flex-1 overflow-y-auto bg-white custom-scrollbar relative">
          <AnimatePresence mode="wait">
            {phase === 'drafting' && (
              <DraftingPanel 
                key="workspace-drafting-panel"
                activeMode={activeMode} 
                activeRole={activeRole} 
                onModeChange={setActiveMode} 
                onRoleChange={setActiveRole} 
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
                error={deductionError}
                timedOut={deductionTimeout}
                onRetry={handleRetryDeduction}
                onBack={handleResetIntent}
              />
            )}
            {phase === 'decision' && (
              <motion.div key="workspace-decision-panel" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <div className="px-4 pt-4 sm:px-8">
                  <TripExecutionPanel
                    tripId={planningResourceId}
                    planId={savedTripId || undefined}
                    routes={dynamicRoutes}
                    onRequestReplan={(node) => {
                      setUserIntent(`保持未完成节点不变，仅因“${node.name}”执行状态变化而重排剩余路线，目标是最小修改。`);
                      setPhase('drafting');
                      setIntentsReady(false);
                    }}
                  />
                  <ReplanProposalPanel
                    proposal={replanProposal}
                    busy={replanBusy}
                    onApply={(candidate) => { void applyReplanCandidate(candidate); }}
                    onReject={() => { void rejectReplanProposal(); }}
                  />
                  <PreTripPlanningPanel
                    userId={currentUser.id}
                    tripId={planningResourceId}
                    destination={targetCityInfo?.name || initialDestination || ''}
                    baseNodes={currentDayRoutes}
                    budget={Number(budgetData?.total_budget || 0)}
                    days={totalDays.length}
                    travelers={Math.max(1, roomMembers.length)}
                  />
                </div>
                {planVariants.length > 1 && (
                  <div className="px-8 pt-4 flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-bold text-slate-500">方案切换：</span>
                    {planVariants.map((v: any) => (
                      <button
                        key={v.id}
                        onClick={() => activePlanId !== v.id && handleSelectPlan(v.id)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors cursor-pointer ${
                          activePlanId === v.id ? 'bg-slate-800 text-white border-slate-800' : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-100'
                        }`}
                        title={v.desc || ''}
                      >
                        {v.name}{v.style === 'relaxed' ? ' · 慢游' : v.style === 'intense' ? ' · 暴走' : v.style === 'niche' ? ' · 小众' : ''}
                      </button>
                    ))}
                    {planSwitchNotice && <span role="status" className="basis-full text-[11px] font-bold text-emerald-600">{planSwitchNotice}</span>}
                  </div>
                )}
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
                  onMarkVisited={handleNodeVisited}
                  visitedNodes={visitedNodes}
                  roomCode={roomCode}
                  roomMembers={roomMembers}
                  teamSatisfaction={teamSatisfaction}
                  arbitrationRecords={arbitrationRecords}
                  nodeVotes={nodeVotes}
                  onVote={handleVote}
                  wsConnected={wsConnected}
                  currentUser={currentUser}
                  onApplyScenario={({ budgetLimit, paceLimit, riskTolerance }: { budgetLimit: number; paceLimit: number; riskTolerance: number }) => {
                    const constraintIntent = `请在下一轮规划中控制人均预算不超过 ¥${budgetLimit}，每天最多安排 ${paceLimit} 个节点，风险容忍度为 ${riskTolerance}。`;
                    setUserIntent((previous: string) => previous ? `${previous}\n${constraintIntent}` : constraintIntent);
                    setPhase('drafting');
                    setIntentsReady(false);
                  }}
                  safetyInfo={safetyInfo}
                  safetyBrief={safetyBrief}
                  briefLoading={briefLoading}
                  onGenerateSafetyBrief={generateSafetyBrief}
                  targetCity={targetCityInfo?.name || ''}
                   onPhotoClick={(poi: {name: string; type?: string; city?: string}) => {
                    setPhotoModalPoi(poi);
                    setActivePhotoSource(0);
                    setPhotoModalOpen(true);
                   }}
                   onSecondaryPlan={(poi: any) => setSecondaryPoi({ ...poi, city: targetCityInfo?.name || '' })}
                 />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      <SecondaryPlanner
        open={Boolean(secondaryPoi)}
        city={secondaryPoi?.city || targetCityInfo?.name || ''}
        poi={secondaryPoi}
        userId={currentUser.id}
        tripId={planningResourceId}
        onClose={() => setSecondaryPoi(null)}
      />

      {/* 右侧：地图与全视图渲染 */}
      <div className="relative flex-1 min-h-[48vh] lg:min-h-0 bg-slate-100 overflow-hidden flex flex-col">
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

        {/* 旅中风险推送中心：轮询实时风险快照，天气/路况/安全事件变更时推送（含目的地风险订阅） */}
        <RiskPushCenter
          city={targetCityInfo?.name || ''}
          coordinate={targetCityInfo?.lnglat}
          safetyInfo={safetyInfo}
          weatherInfo={weatherInfo}
          trafficInfo={trafficInfo}
          userId={currentUser?.id}
          roomCode={roomCode}
          onReplan={(proposal) => {
            void (async () => {
              const signals = Array.isArray(proposal?.changed_signals) ? proposal.changed_signals : [];
              let actionText = '仅对受影响节点执行最小修改重规划';
              try {
                const response = await apiFetch(`${API_BASE}/api/v1/planning/emergency/evaluate`, {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ user_id: currentUser.id, trip_id: planningResourceId, destination: targetCityInfo?.name || '', changed_signals: signals, current_route: dynamicRoutes }),
                });
                const payload = await response.json();
                const action = payload?.data?.actions?.[0] || payload?.actions?.[0];
                if (action?.title) actionText = `${action.title}：${action.reason || ''}`;

                if (!action || action.type === 'manual_review') {
                  setApiError(`${actionText}，请补充具体受影响节点后再重排`);
                  return;
                }

                // The evaluator is deterministic; submit its structured action
                // directly to the minimal-perturbation endpoint. This keeps
                // unaffected nodes intact and avoids a second LLM rewrite.
                const replanResponse = await apiFetch(`${API_BASE}/api/v1/planning/replan`, {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    trip_id: planningResourceId,
                    destination: targetCityInfo?.name || '',
                    existing_route: dynamicRoutes,
                    affected_nodes: action.affected_nodes || [],
                    changed_signals: signals,
                    replacement_kind: action.replacement_kind || 'DESTINATION',
                    member_utilities: Object.values(teamSatisfaction).map((value) => Number(value)).filter((value) => Number.isFinite(value)),
                  }),
                });
                const replanPayload = await replanResponse.json();
                if (!replanResponse.ok) {
                  throw new Error(replanPayload?.error?.message || replanPayload?.error || '结构化重规划失败');
                }
                const proposalId = proposal?.proposal_id || payload?.data?.proposal_id || payload?.proposal_id;
                await apiFetch(`${API_BASE}/api/v1/planning/events`, {
                  method: 'POST', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    trip_id: planningResourceId,
                    event_type: 'replan_proposal_accepted',
                    payload: { proposal_id: proposalId || '', action_id: action.id, action_type: action.type, affected_nodes: action.affected_nodes || [], preserve_nodes: action.preserve_nodes || [] },
                  }),
                });
                const data = replanPayload?.data || replanPayload;
                const candidateCount = Array.isArray(data?.replacement_candidates) ? data.replacement_candidates.length : 0;
                setReplanProposal({
                  proposalId: proposal?.proposal_id || payload?.data?.proposal_id || payload?.proposal_id || '',
                  action,
                  data,
                });
                setApiError(`${actionText}。已保留 ${data?.preserved_nodes ?? 0} 个节点，${candidateCount} 个替代候选待确认`);
                return;
              } catch { /* use the safe generic action text when the evaluator is unavailable */ }
              setUserIntent(`风险中心确认：${actionText}。保留未受影响节点，不改变总目的地。`);
              setApiError(`${actionText}，结构化服务暂不可用，请稍后重试`);
            })();
          }}
        />

        {/* 碳足迹与绿色交通评估（P5） */}
        <CarbonFootprintPanel routes={dynamicRoutes} travelDetails={travelDetails} />

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
                <button data-testid="close-blackboard" onClick={() => setShowBlackboard(false)} className="p-1 hover:bg-slate-200 rounded-md text-slate-400 cursor-pointer" aria-label="关闭博弈黑板"><X className="w-4 h-4"/></button>
              </div>
              <div className="p-6 overflow-y-auto text-xs font-mono text-slate-600 whitespace-pre-wrap custom-scrollbar leading-relaxed">
                {humanReadableLogs}
                {phase === 'deduction' && <span className="inline-block w-2 h-4 bg-orange-500 animate-pulse ml-1 align-middle"></span>}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {showPoster && (
            <motion.div key="poster-modal-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-8">
              <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.9, opacity: 0 }} className="bg-white rounded-2xl shadow-2xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
                <div className="flex items-center justify-between p-4 border-b border-slate-100">
                  <h3 className="font-bold text-slate-800 flex items-center gap-2 text-sm"><Share2 className="w-4 h-4 text-purple-500"/> 行程海报</h3>
                  <button onClick={() => setShowPoster(false)} className="p-1 hover:bg-slate-200 rounded-md text-slate-400 cursor-pointer"><X className="w-4 h-4"/></button>
                </div>
                <div className="p-4">
                  <ItineraryPoster 
                    route={currentDayRoutes.map((r: any) => ({
                      day: r.day,
                      time: r.time,
                      location: r.name,
                      name: r.name,
                      cost_estimate: r.cost,
                      tags: r.tags,
                    }))} 
                    city={targetCityInfo?.name || ''}
                  />
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        <TripDiaryModal open={showDiary} onClose={() => setShowDiary(false)} markdown={diaryMarkdown} city={targetCityInfo?.name || ''} onDownload={downloadDiary} />

        {showBudget && (
          <div className="absolute inset-0 z-[70] flex items-center justify-center bg-slate-950/45 p-4" role="dialog" aria-modal="true" aria-labelledby="budget-panel-title">
            <div className="max-h-[90vh] w-full max-w-[520px] overflow-y-auto" onClick={(event) => event.stopPropagation()}>
              <div className="mb-2 flex items-center justify-between rounded-xl bg-white px-4 py-3 shadow-lg">
                <h3 id="budget-panel-title" className="text-sm font-black text-slate-800">协作预算账本</h3>
                <button type="button" onClick={() => setShowBudget(false)} className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100" title="关闭预算账本" aria-label="关闭预算账本"><X className="h-4 w-4" /></button>
              </div>
              <BudgetPanel userId={currentUser.id} tripId={planningResourceId} />
            </div>
          </div>
        )}
        <ExpenseReportModal open={showExpenseReport} onClose={() => setShowExpenseReport(false)} userId={currentUser.id} tripId={planningResourceId} />
      <SatisfactionModal open={showSatisfaction} onClose={() => setShowSatisfaction(false)} userId={currentUser.id} tripId={planningResourceId} banditArmId={banditArmId} />
        <BookingHub open={showBookingHub} destination={targetCityInfo?.name || initialDestination || ''} onClose={() => setShowBookingHub(false)} />

        <AnimatePresence>
          {feedbackPoi && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 z-[80] flex items-center justify-center bg-slate-950/45 p-4" onClick={() => setFeedbackPoi(null)}>
              <motion.div initial={{ y: 12, opacity: 0 }} animate={{ y: 0, opacity: 1 }} className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-5 shadow-2xl" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="poi-feedback-title">
                <div className="flex items-start justify-between gap-3">
                  <div><h3 id="poi-feedback-title" className="text-sm font-black text-slate-900">告诉我们为何不喜欢</h3><p className="mt-1 text-xs text-slate-500">{feedbackPoi}</p></div>
                  <button type="button" onClick={() => setFeedbackPoi(null)} className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100" title="关闭反馈" aria-label="关闭反馈"><X className="h-4 w-4" /></button>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-2">
                  {['人太多', '距离太远', '太累', '价格不合适', '兴趣不符', '信息不确定'].map((reason) => (
                    <button type="button" key={reason} onClick={() => setFeedbackReason(reason)} className={`min-h-9 rounded-md border px-2 text-xs font-bold transition ${feedbackReason === reason ? 'border-orange-500 bg-orange-50 text-orange-700' : 'border-slate-200 text-slate-600 hover:border-orange-300'}`}>{reason}</button>
                  ))}
                </div>
                <textarea value={feedbackReason && !['人太多', '距离太远', '太累', '价格不合适', '兴趣不符', '信息不确定'].includes(feedbackReason) ? feedbackReason : ''} onChange={(event) => setFeedbackReason(event.target.value)} rows={2} placeholder="其他原因（可选）" className="mt-3 w-full resize-none rounded-md border border-slate-200 px-3 py-2 text-xs outline-none focus:border-orange-400 focus:ring-2 focus:ring-orange-100" />
                <button type="button" onClick={submitPoiFeedback} className="mt-3 min-h-10 w-full rounded-md bg-orange-500 px-4 text-sm font-bold text-white hover:bg-orange-600">提交反馈</button>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* 实景照片搜索 Modal — 精准搜索：城市+景点+实景 */}
        <AnimatePresence>
          {photoModalOpen && photoModalPoi && (() => {
            const city = photoModalPoi.city || '';
            const poi = photoModalPoi.name;
            const preciseQuery = city ? `${city} ${poi} 实景` : `${poi} 实景`;
            const preciseEncoded = encodeURIComponent(preciseQuery);

            const sources = [
              { label: '百度图片', url: `https://image.baidu.com/search/index?tn=baiduimage&word=${preciseEncoded}` },
              { label: 'Google', url: `https://www.google.com/search?q=${preciseEncoded}&tbm=isch` },
              { label: 'Bing', url: `https://www.bing.com/images/search?q=${preciseEncoded}` },
            ];

            return (
            <motion.div
              key="photo-modal-overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-[60] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 sm:p-8"
              onClick={() => setPhotoModalOpen(false)}
            >
              <motion.div
                initial={{ scale: 0.9, opacity: 0, y: 20 }}
                animate={{ scale: 1, opacity: 1, y: 0 }}
                exit={{ scale: 0.9, opacity: 0, y: 20 }}
                transition={{ type: 'spring', damping: 25 }}
                className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[88vh] flex flex-col overflow-hidden"
                onClick={(e) => e.stopPropagation()}
              >
                {/* 标题栏 */}
                <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 bg-slate-50 shrink-0">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="w-8 h-8 rounded-lg bg-emerald-500 flex items-center justify-center shrink-0">
                      <Camera className="w-4 h-4 text-white" />
                    </div>
                    <div className="min-w-0">
                      <h3 className="font-bold text-slate-800 text-sm truncate">{poi}</h3>
                      <p className="text-[11px] text-slate-500 truncate">
                        {city ? `${city} · ` : ''}{photoModalPoi.type || '景点'} · 搜索词：<span className="text-emerald-600 font-bold">{preciseQuery}</span>
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    <a
                      href={sources[activePhotoSource]?.url || sources[0].url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="px-3 py-1.5 rounded-lg bg-orange-50 text-orange-600 text-xs font-bold hover:bg-orange-100 border border-orange-200 transition-colors flex items-center gap-1"
                    >
                      <ExternalLink className="w-3 h-3" /> 新窗口打开
                    </a>
                    <button
                      onClick={() => setPhotoModalOpen(false)}
                      className="p-1.5 hover:bg-slate-200 rounded-lg text-slate-400 cursor-pointer transition-colors"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* 搜索源 Tab 切换 */}
                <div className="flex items-center gap-1 px-5 py-2 border-b border-slate-100 bg-white shrink-0">
                  <span className="text-[10px] text-slate-400 mr-2 font-bold">搜索源：</span>
                  {sources.map((src, i) => (
                    <button
                      key={src.label}
                      onClick={() => setActivePhotoSource(i)}
                      className={`px-3 py-1 rounded-md text-[11px] font-bold border transition-colors ${i === activePhotoSource ? 'bg-emerald-500 text-white border-emerald-500' : 'bg-slate-50 text-slate-500 border-slate-200 hover:bg-slate-100'}`}
                    >
                      {src.label}
                    </button>
                  ))}
                  <div className="flex-1" />
                  <span className="text-[10px] text-slate-400">
                    精准搜索：城市+景点名+实景
                  </span>
                </div>

                  {/* iframe 内容区 */}
                  <div className="flex-1 relative bg-slate-100">
                    <iframe
                      src={sources[activePhotoSource]?.url || sources[0].url}
                      className="w-full h-full border-0"
                      title={`${poi} 实景照片`}
                      /* allow-same-origin is deliberately NOT granted: this
                         document is a third-party search page built from user
                         and POI text. Combining allow-scripts with
                         allow-same-origin would let the framed page reach this
                         origin, defeating the sandbox. Without it the frame
                         gets an opaque origin, which is what we want. */
                      sandbox="allow-scripts allow-popups allow-forms allow-popups-to-escape-sandbox"
                    />
                  </div>

                {/* 底部提示 */}
                <div className="px-5 py-2.5 border-t border-slate-100 bg-slate-50 flex items-center justify-between shrink-0">
                  <span className="text-[11px] text-slate-500">
                    当前搜索词：<span className="font-bold text-emerald-600">{preciseQuery}</span> — 已包含城市名以精确定位
                  </span>
                  <span className="text-[11px] text-slate-400">
                    搜索结果仅供参考，以实际现场为准
                  </span>
                </div>
              </motion.div>
            </motion.div>
            );
          })()}
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

// 出行方式与偏好定位的快捷切换选项（值需与后端 user_preferences.mode / role 完全对齐）
const TRAVEL_MODES = [
  { value: 'solo', label: '一人行', hint: '1', desc: '单人专属节奏' },
  { value: 'coop', label: '亲友结伴', hint: '2', desc: '多智能体博弈平衡' },
  { value: 'pvp', label: '高性价比', hint: '3', desc: '极致体验成本比' },
];
const USER_ROLES = [
  { value: '寻味探索', label: '寻味探索', hint: 'Alt+1', desc: '美食驱动' },
  { value: '视觉体验', label: '视觉体验', hint: 'Alt+2', desc: '出片导向' },
  { value: '休闲漫步', label: '休闲漫步', hint: 'Alt+3', desc: '宽裕漫游' },
  { value: '深度探索', label: '深度探索', hint: 'Alt+4', desc: '紧凑打卡' },
];

function DraftingPanel({ activeMode, activeRole, onModeChange, onRoleChange, onSubmit, isReady, userIntent, setUserIntent, apiError, historyLength, roomMembers }: any) {
  const [listening, setListening] = useState(false);
  const recRef = useRef<any>(null);
  const parsed = useMemo(() => parseVoiceIntent(userIntent || ''), [userIntent]);

  // 快捷键快速切换：数字键 1/2/3 切换出行方式，Alt+1~4 切换偏好定位（输入框聚焦时不触发）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.isContentEditable)) return;
      if (e.altKey) {
        const idx = ['1', '2', '3', '4'].indexOf(e.key);
        if (idx >= 0 && USER_ROLES[idx]) { e.preventDefault(); onRoleChange(USER_ROLES[idx].value); }
        return;
      }
      if (e.ctrlKey || e.metaKey) return;
      const mi = ['1', '2', '3'].indexOf(e.key);
      if (mi >= 0) { e.preventDefault(); onModeChange(TRAVEL_MODES[mi].value); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onModeChange, onRoleChange]);

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

      {/* 快速切换：出行方式 + 偏好定位（与后端 user_preferences 对齐，切换后重新推演即生效） */}
      <div className="mb-6 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">出行方式</span>
          <span className="text-[10px] text-slate-300 font-medium">快捷键 1 / 2 / 3</span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {TRAVEL_MODES.map((m) => (
            <button
              key={m.value}
              onClick={() => onModeChange(m.value)}
              disabled={isReady}
              title={`${m.desc} · 快捷键 ${m.hint}`}
              className={`px-2 py-2 rounded-xl border text-xs font-bold transition-all cursor-pointer flex items-center justify-center gap-1.5 ${activeMode === m.value ? 'border-orange-500 bg-orange-50 text-orange-600 shadow-sm' : 'border-slate-200 bg-white text-slate-600 hover:border-orange-200 hover:bg-orange-50/40'} ${isReady ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span>{m.label}</span>
              <kbd className="text-[9px] px-1 py-0.5 rounded bg-slate-100 text-slate-400 border border-slate-200 font-mono">{m.hint}</kbd>
            </button>
          ))}
        </div>

        <div className="flex items-center justify-between pt-1">
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">偏好定位</span>
          <span className="text-[10px] text-slate-300 font-medium">快捷键 Alt+1~4</span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {USER_ROLES.map((r) => (
            <button
              key={r.value}
              onClick={() => onRoleChange(r.value)}
              disabled={isReady}
              title={`${r.desc} · 快捷键 ${r.hint}`}
              className={`px-3 py-2 rounded-xl border text-xs font-bold transition-all cursor-pointer flex items-center justify-between ${activeRole === r.value ? 'border-orange-500 bg-orange-50 text-orange-600 shadow-sm' : 'border-slate-200 bg-white text-slate-600 hover:border-orange-200 hover:bg-orange-50/40'} ${isReady ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span>{r.label}</span>
              <kbd className="text-[9px] px-1 py-0.5 rounded bg-slate-100 text-slate-400 border border-slate-200 font-mono">{r.hint}</kbd>
            </button>
          ))}
        </div>
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
          
          <VoiceIntentChips parsed={parsed} />
          <textarea 
            data-testid="intent-input"
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

interface DeductionPanelProps {
  latestLog?: string;
  error?: string | null;
  timedOut?: boolean;
  onRetry?: () => void;
  onBack?: () => void;
}

function DeductionPanel({ latestLog, error, timedOut, onRetry, onBack }: DeductionPanelProps) {
  const hasError = Boolean(error || timedOut);
  const displayError = error || (timedOut ? "推演超时：长时间未收到智能体响应，可能是网络波动或模型服务繁忙。" : null);

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center py-20 h-full text-center px-8">
      {/* 状态图标 */}
      <div className="relative w-24 h-24 mb-6">
        {hasError ? (
          <>
            <div className="absolute inset-0 flex items-center justify-center">
              <AlertCircle className="w-12 h-12 text-red-500" />
            </div>
            <motion.div animate={{ scale: [1, 1.15, 1] }} transition={{ duration: 1.5, repeat: Infinity }} className="absolute inset-0 border-2 border-red-400/60 rounded-full" />
          </>
        ) : (
          <>
            <motion.div animate={{ rotate: 360 }} transition={{ duration: 3, repeat: Infinity, ease: "linear" }} className="absolute inset-0 border-2 border-dashed border-orange-400 rounded-full" />
            <div className="absolute inset-0 flex items-center justify-center">
              <RefreshCw className="w-6 h-6 text-orange-500 animate-spin" />
            </div>
          </>
        )}
      </div>

      <h3 className={`text-base font-black ${hasError ? 'text-red-600' : 'text-slate-800'}`}>
        {hasError ? '推演遇到问题' : '多智能体正在博弈调和众口诉求...'}
      </h3>
      
      {/* 实时推演日志卡片 */}
      <div className={`mt-4 p-3.5 rounded-2xl text-xs font-mono max-w-sm w-full border shadow-md ${
        hasError ? 'bg-red-950/90 border-red-800 text-red-100' : 'bg-slate-900 border-slate-800 text-slate-200'
      }`}>
        <div className={`flex items-center gap-1.5 font-bold mb-1 ${hasError ? 'text-red-400' : 'text-orange-400'}`}>
          <span className={`w-2 h-2 rounded-full ${hasError ? 'bg-red-500' : 'bg-orange-500 animate-ping'}`}></span>
          <span>{hasError ? '系统提示:' : '智能体实时推演中:'}</span>
        </div>
        <p className={`text-[11px] leading-relaxed line-clamp-3 ${hasError ? 'text-red-200' : 'text-slate-300'}`}>
          {hasError ? displayError : (latestLog || "全息雷达数据已捕获，多智能体正在计算拓扑并交织生成行程...")}
        </p>
      </div>

      {/* 错误/超时状态下的操作按钮 */}
      {hasError && (
        <div className="mt-5 flex gap-3">
          <button
            onClick={onRetry}
            className="px-5 py-2.5 bg-orange-500 hover:bg-orange-600 text-white text-sm font-bold rounded-xl shadow-lg hover:shadow-xl transition-all hover:scale-105 flex items-center gap-2 cursor-pointer"
          >
            <RotateCw className="w-4 h-4" />
            重新推演
          </button>
          <button
            onClick={onBack}
            className="px-5 py-2.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 text-sm font-bold rounded-xl shadow hover:shadow-md transition-all cursor-pointer flex items-center gap-2"
          >
            <ArrowLeft className="w-4 h-4" />
            返回修改
          </button>
        </div>
      )}

      {/* 正常推演时显示提示 */}
      {!hasError && (
        <p className="mt-3 text-[11px] text-slate-400">
          通常需要 20-60 秒，正在协调地理/风控/知识/调度多智能体共识...
        </p>
      )}
    </motion.div>
  );
}

// ============== 同行网络协同沙盘 · 实时动态版 ==============
type ActivityEvt = { id: string; ts: number; type: 'vote' | 'join' | 'consensus' | 'typing' | 'split'; text: string };

function TeamPresenceBar({ members = [], teamSatisfaction = {}, roomCode, arbitrationRecords = [], wsConnected = true, currentUser = null }: any) {
  const [liveSat, setLiveSat] = useState<Record<string, number>>({});
  const [onlineMap, setOnlineMap] = useState<Record<string, boolean>>({});
  const [feed] = useState<ActivityEvt[]>([]);
  const [copied, setCopied] = useState(false);

  // 初始化：同步真实满意度与在线状态（不再随机生成假数据）
  useEffect(() => {
    const init: Record<string, number> = {};
    const onlineInit: Record<string, boolean> = {};
    members.forEach((m: any, idx: number) => {
      const k = m.name;
      const ext = Number(teamSatisfaction[k]);
      init[k] = Number.isFinite(ext) && ext > 0 ? ext : 90;
      onlineInit[k] = true; // 房间成员默认可用
    });
    setLiveSat(init);
    setOnlineMap(onlineInit);
  }, [members.length, roomCode, teamSatisfaction]);

  // 外部满意度有更新时同步（真实数据，不做随机抖动）
  useEffect(() => {
    setLiveSat(prev => {
      const next = { ...prev };
      Object.entries(teamSatisfaction || {}).forEach(([k, v]) => {
        const n = Number(v);
        if (Number.isFinite(n) && n > 0) next[k] = Math.max(60, Math.min(99, n));
      });
      return next;
    });
  }, [teamSatisfaction]);

  const avgSatisfaction = useMemo(() => {
    const vals = Object.values(liveSat).map(v => Number(v)).filter(v => Number.isFinite(v) && v > 0);
    return vals.length > 0 ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) : 94;
  }, [liveSat]);

  const copyRoom = async () => {
    try {
      await navigator.clipboard.writeText(roomCode || '');
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const getSatColor = (s: number) =>
    s >= 90 ? 'text-emerald-600' : s >= 78 ? 'text-teal-600' : s >= 65 ? 'text-amber-600' : 'text-rose-500';
  const getSatBar = (s: number) =>
    s >= 90 ? 'from-emerald-400 to-teal-400' : s >= 78 ? 'from-teal-400 to-cyan-400' : s >= 65 ? 'from-amber-400 to-orange-400' : 'from-rose-400 to-pink-400';

  // 👑 头像渲染：优先使用用户上传的真实头像，没有时再用 DiceBear 生成
  const getAvatarUrl = (m: any) => {
    let url = m.avatarUrl || m.avatar_url || '';
    // 如果成员是当前用户本人且没有头像，用 currentUser 的兜底
    if (!url && m.id === currentUser?.id && currentUser?.avatarUrl) {
      url = currentUser.avatarUrl;
    }
    // 相对路径补全为绝对路径
    if (url && !url.startsWith('http') && !url.startsWith('data:')) {
      url = `${API_BASE}${url}`;
    }
    if (url) return url;
    return `https://api.dicebear.com/9.x/avataaars/svg?seed=${encodeURIComponent(m.avatarSeed || m.name)}&backgroundColor=fdeed8`;
  };

  return (
    <div className="mb-6 rounded-2xl overflow-hidden relative bg-white border border-slate-200 shadow-sm">
      {/* ============ HEADER ============ */}
      <div className="relative px-5 pt-4 pb-3 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-orange-50 border border-orange-200 flex items-center justify-center">
            <Users className="w-[18px] h-[18px] text-orange-500" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-black text-[15px] text-slate-800 tracking-wide">同行伙伴</h3>
              <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-orange-50 text-orange-600 border border-orange-200">
                {members.length} 人
              </span>
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                wsConnected
                  ? 'text-emerald-600 bg-emerald-50'
                  : 'text-rose-500 bg-rose-50'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-emerald-500' : 'bg-rose-400'}`} />
                {wsConnected ? '在线' : '离线'}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* 团队满意度 */}
          <div className="flex items-center gap-2.5 bg-slate-50 border border-slate-100 rounded-xl px-3 py-2">
            <div className="relative w-10 h-10 shrink-0">
              <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                <circle cx="18" cy="18" r="15" fill="none" stroke="#e2e8f0" strokeWidth="3" />
                <circle
                  cx="18" cy="18" r="15" fill="none"
                  stroke={avgSatisfaction >= 90 ? '#10b981' : avgSatisfaction >= 78 ? '#14b8a6' : avgSatisfaction >= 65 ? '#f59e0b' : '#f43f5e'}
                  strokeWidth="3" strokeLinecap="round"
                  strokeDasharray={`${(avgSatisfaction / 100) * 94.25} 94.25`}
                  style={{ transition: 'stroke-dasharray 0.6s ease, stroke 0.6s ease' }}
                />
              </svg>
              <div className="absolute inset-0 flex items-center justify-center">
                <span className={`text-[11px] font-black ${getSatColor(avgSatisfaction)}`}>{avgSatisfaction}</span>
              </div>
            </div>
            <div>
              <p className="text-[11px] font-bold text-slate-700">团队满意度</p>
            </div>
          </div>

          {/* 房间号 */}
          <button
            onClick={copyRoom}
            className="group flex items-center gap-1.5 bg-orange-50 border border-orange-200 hover:bg-orange-100 rounded-xl px-3 py-2 transition-colors cursor-pointer"
            title="点击复制房间号"
          >
            <Radio className="w-3.5 h-3.5 text-orange-500" />
            <span className="text-[11px] font-bold text-orange-600 tracking-wider">{roomCode}</span>
            {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3 text-orange-400 group-hover:text-orange-600" />}
          </button>
        </div>
      </div>

      {/* ============ MEMBERS GRID ============ */}
      <div className="relative px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-1.5">
            <Users className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-[11px] font-bold text-slate-500">成员状态</span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {members.map((m: any, idx: number) => {
            const score = typeof liveSat[m.name] === 'number' ? liveSat[m.name] : 90;
            const isOnline = onlineMap[m.name] !== false;
            const roleColor =
              m.role?.includes('寻味') || m.role?.includes('美食') ? 'text-orange-600 bg-orange-50 border-orange-200'
              : m.role?.includes('摄影') || m.role?.includes('风景') ? 'text-emerald-600 bg-emerald-50 border-emerald-200'
              : m.role?.includes('住宿') || m.role?.includes('品质') ? 'text-indigo-600 bg-indigo-50 border-indigo-200'
              : 'text-slate-600 bg-slate-50 border-slate-200';
            return (
              <div
                key={idx}
                className="relative bg-slate-50/80 border border-slate-100 hover:border-orange-200 rounded-xl p-3 transition-all group"
              >
                <div className="flex items-center gap-3">
                  <div className="relative shrink-0">
                    <img
                      src={getAvatarUrl(m)}
                      alt={m.name}
                      className="w-10 h-10 rounded-full bg-white border border-slate-200 object-cover"
                    />
                    <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white ${isOnline ? 'bg-emerald-400' : 'bg-slate-300'}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="font-bold text-sm text-slate-800 truncate max-w-[90px]">{m.name}</span>
                      {m.role && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-bold shrink-0 ${roleColor}`}>
                          {m.role}
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-slate-400 truncate mt-0.5">{m.intent || '期待同行'}</p>
                    {/* 满意度进度条 */}
                    <div className="mt-2 flex items-center gap-2">
                      <div className="flex-1 h-1.5 rounded-full bg-slate-200 overflow-hidden">
                        <div
                          className={`h-full rounded-full bg-gradient-to-r ${getSatBar(score)} transition-all duration-700`}
                          style={{ width: `${score}%` }}
                        />
                      </div>
                      <span className={`text-[10px] font-bold w-6 text-right ${getSatColor(score)}`}>{score}</span>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ============ 动态 + 协调记录 ============ */}
      {(arbitrationRecords?.length > 0 || feed.length > 0) && (
        <div className="relative grid grid-cols-1 md:grid-cols-2 gap-0 border-t border-slate-100">
          {/* 最新动态 */}
          {feed.length > 0 && (
            <div className="px-5 py-3 border-b md:border-b-0 md:border-r border-slate-100">
              <div className="flex items-center gap-1.5 mb-2">
                <Activity className="w-3.5 h-3.5 text-slate-400" />
                <span className="text-[11px] font-bold text-slate-500">最新动态</span>
              </div>
              <div className="space-y-1.5 max-h-28 overflow-y-auto custom-scrollbar pr-1">
                {feed.slice(0, 5).map((ev) => (
                  <div key={ev.id} className="flex items-start gap-2 text-[11px] leading-snug text-slate-600">
                    <span className="shrink-0 text-slate-400 mt-0.5">·</span>
                    <span className="flex-1">{ev.text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 行程协调记录 */}
          {arbitrationRecords?.length > 0 && (
            <div className="px-5 py-3">
              <div className="flex items-center gap-1.5 mb-2">
                <Scale className="w-3.5 h-3.5 text-slate-400" />
                <span className="text-[11px] font-bold text-slate-500">行程协调</span>
              </div>
              <div className="space-y-1.5 max-h-28 overflow-y-auto custom-scrollbar pr-1">
                {arbitrationRecords.map((rec: string, rIdx: number) => (
                  <div key={rIdx} className="flex items-start gap-2 text-[11px] leading-snug">
                    <span className="shrink-0 w-1 h-1 rounded-full bg-orange-400 mt-1.5" />
                    <span className="text-slate-600">{rec}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
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
              <Navigation className="w-3.5 h-3.5" /> 点击查看高德实景导航
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
              <div className="text-[11px] text-slate-300 leading-relaxed pt-1">
                该路段暂未获取到逐字导航指引。
              </div>
            )}
            {/* 👑 无论有无逐字指引，始终显示高德实时导航按钮 */}
            <div className="pt-2 border-t border-slate-800">
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
                <a
                  href={`https://www.amap.com/search?query=${encodeURIComponent(nextPt?.name || '')}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-orange-500/90 hover:bg-orange-500 text-white font-bold transition-colors"
                >
                  <Navigation className="w-3.5 h-3.5" /> 打开高德搜索导航
                </a>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function MafengwoStylePanel({ 
  routes, totalDays, activeDayIndex, onSelectDay, travelDetails, selectedPoiIndex, 
  onSelectPoi, weatherInfo, trafficInfo, onReset, consensusSummary, budgetData, 
  onSwapPoi, onMarkVisited, visitedNodes = [], roomCode, roomMembers, teamSatisfaction, arbitrationRecords, nodeVotes, onVote,
  wsConnected = true,
  safetyInfo, safetyBrief, briefLoading, onGenerateSafetyBrief, targetCity,
  onPhotoClick, currentUser, onApplyScenario, onSecondaryPlan
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
          wsConnected={wsConnected}
          currentUser={currentUser}
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
        <ConsensusExplainability
          route={routes || []}
          members={(roomMembers || []).map((member: any) => ({ id: member.id, name: member.name, interestTags: member.intent ? [member.intent] : [] }))}
        />
        <ScenarioSimulator
          route={routes || []}
          members={(roomMembers || []).map((member: any) => ({
            id: member.id,
            name: member.name,
            interestTags: member.intent ? [member.intent] : [],
            budgetWeight: Number(member.budgetWeight ?? member.budget_weight ?? 1),
            paceWeight: Number(member.paceWeight ?? member.pace_weight ?? 1),
            riskWeight: Number(member.riskWeight ?? member.risk_weight ?? 1),
          }))}
          onApply={onApplyScenario}
        />

        {weatherInfo && (
          <div className="mb-8 p-4 bg-gradient-to-r from-orange-50/90 to-amber-50/60 border border-orange-100 rounded-2xl shadow-2xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-orange-500 text-white rounded-xl shadow-xs"><Sun className="w-5 h-5"/></div>
                <div>
                  <h4 className="text-sm font-black text-slate-800">目的地气象：{weatherInfo.condition}</h4>
                  <p className="text-xs text-slate-500 mt-0.5 font-medium">{trafficInfo?.advice || '暂无供应商路况数据，请在出行前重新获取'}</p>
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

        {safetyInfo && (
          <div className="mb-8 p-4 bg-gradient-to-r from-green-50/90 to-emerald-50/60 border border-green-100 rounded-2xl shadow-2xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-green-500 text-white rounded-xl shadow-xs"><ShieldCheck className="w-5 h-5"/></div>
                <div>
                  <h4 className="text-sm font-black text-slate-800">
                    {targetCity ? `${targetCity}安全态势` : '目的地安全态势'}
                    {safetyInfo.risk_level && (
                      <span className={`ml-2 px-2 py-0.5 rounded-full text-[10px] font-bold ${
                        safetyInfo.risk_level === '低风险' ? 'bg-green-500/20 text-green-700'
                        : safetyInfo.risk_level === '中风险' ? 'bg-yellow-500/20 text-yellow-700'
                        : 'bg-red-500/20 text-red-700'
                      }`}>{safetyInfo.risk_level}</span>
                    )}
                  </h4>
                  <p className="text-xs text-slate-500 mt-0.5 font-medium">
                    {safetyInfo.cii_score !== undefined ? `CII 安全指数：${safetyInfo.cii_score}` : '安全数据加载中'}
                  </p>
                </div>
              </div>
              <button 
                onClick={onGenerateSafetyBrief}
                disabled={briefLoading}
                className="text-xs font-bold text-green-600 hover:text-green-700 flex items-center gap-1.5 bg-green-100/80 px-3 py-1.5 rounded-lg border border-green-200 hover:bg-green-200 transition-colors cursor-pointer disabled:opacity-50"
              >
                {briefLoading ? (
                  <><RefreshCw className="w-3.5 h-3.5 animate-spin"/> 生成中...</>
                ) : (
                  <><BrainCircuit className="w-3.5 h-3.5"/> 生成安全简报</>
                )}
              </button>
            </div>
            {safetyBrief && (
              <motion.div 
                initial={{ height: 0, opacity: 0 }} 
                animate={{ height: 'auto', opacity: 1 }} 
                className="overflow-hidden mt-3 pt-3 border-t border-green-200/60"
              >
                <div className="bg-white/80 p-3 rounded-xl border border-green-100/60">
                  <p className="text-xs text-slate-700 leading-relaxed whitespace-pre-wrap font-medium">{safetyBrief}</p>
                </div>
              </motion.div>
            )}
            {safetyInfo.active_alerts && safetyInfo.active_alerts.length > 0 && (
              <div className="mt-3 pt-3 border-t border-green-200/60">
                <div className="flex flex-wrap gap-1.5">
                  {safetyInfo.active_alerts.map((alert: any, aIdx: number) => (
                    <span key={`alert-${aIdx}`} className="inline-flex items-center gap-1 bg-amber-50 text-amber-700 text-[10px] px-2 py-0.5 rounded-md font-bold border border-amber-200">
                      <AlertCircle className="w-3 h-3"/> {alert?.title || alert?.type || '告警'}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <h2 className="text-2xl font-black text-slate-800 mb-8 tracking-tight">第 {totalDays[activeDayIndex] || 1} 天 行程安排</h2>

        <div className="relative pl-6">
          <div className="absolute top-3 bottom-6 left-[11px] w-[2px] bg-slate-200/80"></div>

          {Array.isArray(routes) && routes.map((pt: any, idx: number) => {
            const isSelected = selectedPoiIndex === idx;

            const validPhotoUrls = (pt.photos || []).filter((u: string) => Boolean(u && (u.startsWith('http') || u.startsWith('//'))));
            const fallbackImg = getCleanPhotoUrl(pt.map_image || '');

            // 👑 地点详情链接：优先后端回填的 amap_url，缺失时回退到高德关键词搜索，保证"查看详情"永远可点击
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
                        ⭐ {pt.rating || '暂无供应商数据'}
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
                        data-testid={`vote-up-${idx}`}
                        onClick={(e) => { e.stopPropagation(); onVote(pt.name, 'up'); }}
                        className={`p-1.5 rounded-lg border text-xs font-bold flex items-center gap-1 transition-colors cursor-pointer ${nodeVotes[pt.name] === 'up' ? 'bg-emerald-500 text-white border-emerald-500' : 'bg-slate-50 hover:bg-emerald-50 text-slate-500 hover:text-emerald-600 border-slate-200'}`}
                        title="赞同该节点安排"
                      >
                        <ThumbsUp className="w-3 h-3" />
                      </button>

                      <button
                        data-testid={`vote-down-${idx}`}
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

                      {onSecondaryPlan && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onSecondaryPlan(pt); }}
                          className="flex items-center gap-1 rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-bold text-emerald-700 transition-colors hover:bg-emerald-100"
                          title="规划景区内特色点与步行顺序"
                        >
                          <Footprints className="h-3 w-3" />
                          <span>景区内规划</span>
                        </button>
                      )}

                      {onMarkVisited && (
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); onMarkVisited(pt); }}
                          className={`flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs font-bold transition-colors ${visitedNodes.includes(`${pt.day || 1}:${pt.name || ''}`) ? 'border-emerald-300 bg-emerald-100 text-emerald-700' : 'border-slate-200 bg-slate-50 text-slate-500 hover:border-emerald-200 hover:bg-emerald-50 hover:text-emerald-700'}`}
                          title="明确标记已到访，写入行程反馈事件"
                        >
                          <CheckCircle2 className="h-3 w-3" />
                          <span>{visitedNodes.includes(`${pt.day || 1}:${pt.name || ''}`) ? '已到访' : '标记到访'}</span>
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
                    {(() => {
                      const vPhotos = (pt.photos || []).filter((u: string) => Boolean(getCleanPhotoUrl(u))).map((u: string) => getCleanPhotoUrl(u));
                      const slots: (string | null)[] = [vPhotos[0] || null, vPhotos[1] || null, vPhotos[2] || null];
                      const hasAny = vPhotos.length > 0 || fallbackImg;
                      const handlePhotoClick = () => {
                        if (onPhotoClick) {
                          onPhotoClick({ name: pt.name, type: pt.tags?.[0] || pt.type, city: targetCity });
                        }
                      };

                      if (!hasAny) {
                        return (
                          <div className="col-span-3 rounded-xl overflow-hidden aspect-[4/3]">
                            <PoiImage photos={[]} mapImage="" amapUrl={pt.amap_url} name={pt.name} type={pt.tags?.[0] || pt.type} className="w-full h-full" index={idx} onPhotoClick={handlePhotoClick} />
                          </div>
                        );
                      }
                      return slots.map((src, imgIdx) => (
                        <div key={`img-thumb-${idx}-${imgIdx}`} className={`rounded-xl overflow-hidden bg-slate-100 border border-slate-100 shadow-2xs ${imgIdx === 0 && vPhotos.length === 1 ? 'col-span-3 aspect-[16/10]' : 'aspect-[4/3]'}`}>
                          <PoiImage
                            photos={src ? [src] : []}
                            mapImage={fallbackImg}
                            amapUrl={pt.amap_url}
                            name={pt.name}
                            type={pt.tags?.[0] || pt.type}
                            className="w-full h-full"
                            index={imgIdx === 0 ? idx : undefined}
                            onPhotoClick={handlePhotoClick}
                          />
                        </div>
                      ));
                    })()}
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
    <div className="w-full bg-gradient-to-t from-slate-950/95 via-slate-900/60 to-transparent pt-16 pb-6 px-8 backdrop-blur-sm">
      <div className="max-w-6xl mx-auto mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-1 h-4 bg-gradient-to-b from-orange-400 to-rose-500 rounded-full" />
          <span className="text-white/90 text-xs font-bold tracking-wider">行程节点</span>
          <span className="text-white/40 text-[10px] font-mono">{routes.length} POIs</span>
        </div>
        <div className="flex items-center gap-1 text-white/30 text-[10px]">
          <ChevronLeft className="w-3 h-3" />
          <span>横向滑动浏览</span>
          <ChevronRight className="w-3 h-3" />
        </div>
      </div>
      <div ref={scrollContainerRef} className="flex gap-3 overflow-x-auto custom-scrollbar pb-3 snap-x snap-mandatory max-w-6xl mx-auto" style={{scrollbarWidth:'thin'}}>
        {routes.map((r: any, idx: number) => {
          const isSelected = selectedIndex === idx;
          const typeLabel = r.tags?.[0] || (r.type?.includes('餐') ? '餐饮美食' : r.type?.includes('酒店') ? '品质住宿' : '风景名胜');
          const isFood = typeLabel.includes('餐') || typeLabel.includes('食');
          const isHotel = typeLabel.includes('宿') || typeLabel.includes('酒店');

          return (
            <div
              key={`carousel-card-item-${r.name}-${idx}`}
              onClick={() => onSelect(idx)}
              className={`snap-center shrink-0 w-[210px] h-[130px] rounded-2xl overflow-hidden relative cursor-pointer transition-all duration-500 transform ${
                isSelected
                  ? 'ring-2 ring-orange-400 ring-offset-2 ring-offset-slate-950 scale-110 shadow-[0_0_30px_rgba(251,146,60,0.4)] z-20'
                  : 'opacity-70 hover:opacity-100 hover:scale-105 shadow-lg hover:shadow-xl'
              }`}
            >
              <RealPoiImage
                photoUrl={r.photos?.[0] || ''}
                poiName={r.name}
                type={typeLabel}
                className="absolute inset-0 w-full h-full"
              />
              
              {/* 精致渐变遮罩 */}
              <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent pointer-events-none" />
              {isSelected && (
                <div className="absolute inset-0 ring-1 ring-inset ring-white/20 rounded-2xl pointer-events-none" />
              )}
              
              {/* 序号标记 */}
              <div className={`absolute top-2 left-2 w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-black shadow-lg backdrop-blur-sm transition-all ${
                isSelected ? 'bg-gradient-to-br from-orange-400 to-rose-500 text-white scale-110' : 'bg-black/40 text-white/90'
              }`}>
                {idx + 1}
              </div>
              
              {/* 类型标签 */}
              <div className={`absolute top-2 right-2 px-2 py-0.5 rounded-full text-[9px] font-black backdrop-blur-md ${
                isFood ? 'bg-orange-500/80 text-white' : isHotel ? 'bg-indigo-500/80 text-white' : 'bg-emerald-500/80 text-white'
              }`}>
                {typeLabel.length > 4 ? typeLabel.slice(0, 4) : typeLabel}
              </div>
              
              {/* 名称与信息 */}
              <div className="absolute bottom-0 left-0 right-0 p-3 pointer-events-none">
                <h4 className="text-white font-bold text-[13px] leading-tight truncate drop-shadow-lg">{r.name}</h4>
                <div className="flex items-center gap-2 mt-1">
                  {r.rating && (
                    <span className="flex items-center gap-0.5 text-amber-300 text-[10px] font-bold">
                      <Star className="w-2.5 h-2.5 fill-amber-300" />
                      {r.rating}
                    </span>
                  )}
                  {r.cost && (
                    <span className="text-white/60 text-[10px] font-medium truncate">{typeof r.cost === 'string' ? r.cost.replace(/^预估花费：/, '') : ''}</span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
