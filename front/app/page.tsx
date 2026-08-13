'use client';

import dynamic from 'next/dynamic';
import React, { useState, useRef, useEffect, useMemo } from 'react';
import { 
  Users, MapPin, Sparkles, Coffee, Camera, Car, PiggyBank, ArrowRight, Check, 
  UserPlus, Map as MapIcon, Compass, Headphones, TrendingDown, RefreshCw, 
  X, Play, AlertCircle, Clock, ThumbsUp, BrainCircuit,
  MessageSquare, Wand2, History, ChevronDown, ChevronUp, ArrowDown, Navigation, Sun, CloudRain, ExternalLink, Hotel, RotateCw
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// 引入 3D 到 2D 的全域路由调度器
import FullRouteVisualizer from './FullRouteVisualizer';

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

// 👑 智能 JSON 提解助手：全局完整 JSON 解析
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

  // 清理 Markdown 标记
  jsonCandidate = jsonCandidate.replace(/```json/g, "").replace(/```/g, "").trim();

  // 尝试匹配最后一个完整的 } 闭合符
  const lastBraceIdx = jsonCandidate.lastIndexOf("}");
  if (lastBraceIdx === -1) return null;

  const validSubstring = jsonCandidate.substring(0, lastBraceIdx + 1);
  try {
    return JSON.parse(validSubstring);
  } catch (e) {
    return null;
  }
}

// 👑 核心解决卡顿：流式增量提取解析器！只要大模型吐出单条 POI 对象，无需等待全文结尾，秒级提取！
function extractStreamingRoutes(text: string): any[] {
  if (!text) return [];
  let jsonPart = text;
  if (text.includes("[FINAL_JSON]")) {
    jsonPart = text.split("[FINAL_JSON]")[1];
  } else if (text.includes("```json")) {
    jsonPart = text.split("```json")[1];
  }

  // 正则精准捕获包含 location 或 name 的独立 JSON 对象块
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
          lnglat: item.lnglat || [104.06, 30.67],
          color: item.tags?.includes("寻味") || item.type === "food" ? "#f97316" : "#3b82f6",
          desc: item.desc || item.action || "",
          time: item.time || "",
          time_reason: item.time_reason || "",
          transport: item.transport || "",
          tags: item.tags || [],
          cost: item.cost_estimate || "",
          photos: item.photos || [],
          trust_reason: item.trust_reason || "核心地标推荐",
          amap_url: item.amap_url || "",
          hotel_candidates: item.hotel_candidates || []
        });
      }
    } catch (e) {
      // 当前单条对象未生成完毕
    }
  }
  return parsedItems;
}

// 👑 智能实景图片分类与图集离散匹配：彻底解决长城/公园显示路边摊与图片重复问题
function getCleanPhotoUrl(photoUrl?: string, poiName: string = '', photoIndex: number = 0) {
  // 过滤高德返回的脏占位图或路边菜摊脏图
  const isJunkUrl = photoUrl && (
    photoUrl.includes('-0') || photoUrl.includes('-1') || photoUrl.includes('-2') ||
    photoUrl.includes('market') || photoUrl.includes('shop') || photoUrl.includes('stall')
  );

  const isSuperLandmark = poiName.includes('长城') || poiName.includes('故宫') || poiName.includes('北海公园') || poiName.includes('颐和园') || poiName.includes('天坛') || poiName.includes('熊猫基地');

  // 若不是脏图且不是著名超级地标，可优先显示高德实景
  if (photoUrl && photoUrl.startsWith('http') && !isJunkUrl && !isSuperLandmark) {
    return photoUrl;
  }

  // 👑 深度品类 + 图集索引 (photoIndex) 差异化离散高清图库
  if (poiName.includes('长城') || poiName.includes('八达岭') || poiName.includes('居庸关') || poiName.includes('慕田峪')) {
    const wallPhotos = [
      'https://images.unsplash.com/photo-1508804185872-d7badad00f7d?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1547981609-4b6bfe67ca0b?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1529655683826-aba9b3e77383?auto=format&fit=crop&w=600&q=80'
    ];
    return wallPhotos[photoIndex % wallPhotos.length];
  }

  if (poiName.includes('北海') || poiName.includes('颐和园') || poiName.includes('皇家园林') || poiName.includes('公园') || poiName.includes('植物园')) {
    const gardenPhotos = [
      'https://images.unsplash.com/photo-1584252520626-64e031023793?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1599839575945-a9e5af0c3fa5?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=600&q=80'
    ];
    return gardenPhotos[photoIndex % gardenPhotos.length];
  }

  if (poiName.includes('故宫') || poiName.includes('天坛') || poiName.includes('恭王府') || poiName.includes('博物馆') || poiName.includes('展览馆')) {
    const museumPhotos = [
      'https://images.unsplash.com/photo-1566127444979-b3d2b654e3d7?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1584252520626-64e031023793?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1548013146-72479768bada?auto=format&fit=crop&w=600&q=80'
    ];
    return museumPhotos[photoIndex % museumPhotos.length];
  }

  if (poiName.includes('餐') || poiName.includes('店') || poiName.includes('美食') || poiName.includes('火锅') || poiName.includes('羊肉') || poiName.includes('烤') || poiName.includes('小吃') || poiName.includes('酒楼')) {
    const foodPhotos = [
      'https://images.unsplash.com/photo-1555396273-367ea4eb4db5?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1563245372-f21724e3856d?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=600&q=80'
    ];
    return foodPhotos[photoIndex % foodPhotos.length];
  }

  if (poiName.includes('酒店') || poiName.includes('民宿') || poiName.includes('客栈') || poiName.includes('度假')) {
    const hotelPhotos = [
      'https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?auto=format&fit=crop&w=600&q=80',
      'https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=600&q=80'
    ];
    return hotelPhotos[photoIndex % hotelPhotos.length];
  }

  const defaultPhotos = [
    'https://images.unsplash.com/photo-1469854523086-cc02fe5d8800?auto=format&fit=crop&w=600&q=80',
    'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=600&q=80',
    'https://images.unsplash.com/photo-1488646953014-85cb44e25828?auto=format&fit=crop&w=600&q=80'
  ];
  return defaultPhotos[photoIndex % defaultPhotos.length];
}

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

type Phase = 'drafting' | 'deduction' | 'decision';

class SafeErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean; errorMsg: string }> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, errorMsg: '' };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, errorMsg: error.message };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('页面渲染异常:', error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen w-screen flex items-center justify-center bg-slate-50">
          <div className="bg-white rounded-3xl p-10 shadow-xl border border-rose-100 max-w-md text-center">
            <div className="w-14 h-14 bg-rose-50 text-rose-500 rounded-full flex items-center justify-center mx-auto mb-5">
              <AlertCircle className="w-7 h-7" />
            </div>
            <h2 className="text-xl font-black text-slate-800 mb-2">渲染异常</h2>
            <p className="text-sm text-slate-500 mb-6 font-medium">{this.state.errorMsg || '页面组件发生未预期错误'}</p>
            <button
              onClick={() => { this.setState({ hasError: false, errorMsg: '' }); window.location.reload(); }}
              className="px-8 py-3 bg-slate-900 text-white rounded-xl font-bold text-sm hover:bg-orange-600 transition-all shadow-md cursor-pointer"
            >
              <RefreshCw className="w-4 h-4 inline mr-2" /> 刷新页面
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function ContextualLobby() {
  const [selectedMode, setSelectedMode] = useState<string>('coop');
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [roomCode, setRoomCode] = useState<string>('------');
  const [isConnecting, setIsConnecting] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);

  useEffect(() => {
    const generateCode = () => Math.random().toString(36).substring(2, 8).toUpperCase();
    setRoomCode(generateCode());
  }, []);

  const handleBeginPlan = () => setShowAuthModal(true);

  const handleConfirmSync = () => {
    if (!selectedRole) return;
    setIsConnecting(true);
    setTimeout(() => {
        setIsConnecting(false);
        setShowAuthModal(false);
        setIsTransitioning(false);
        setShowWorkspace(true);
    }, 1200); 
  };

  const renderRightPanel = () => {
    switch (selectedMode) {
      case 'solo':
        return (
          <motion.div key="solo-panel-mode" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.3 }} className="flex flex-col h-full">
            <div className="p-7 pb-5 border-b border-slate-200/60 bg-white/50">
              <h2 className="text-xl font-bold text-slate-800 mb-1 flex items-center gap-2"><Compass className="w-5 h-5 text-emerald-600"/> 私人探索雷达</h2>
              <p className="text-xs text-slate-500 font-medium">系统已屏蔽外界干扰，专注为您构建沉浸式路线</p>
            </div>
            <div className="p-6 flex-1 flex flex-col gap-4">
              <div className="p-4 rounded-xl bg-white border border-slate-100 shadow-sm flex items-start gap-4">
                <div className="p-2 bg-emerald-50 rounded-lg text-emerald-600"><Headphones className="w-5 h-5"/></div>
                <div>
                  <p className="text-sm font-bold text-slate-800">深度沉浸开启</p>
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
            <div className="p-7 pb-5 border-b border-slate-200/60 bg-white/50">
              <h2 className="text-xl font-bold text-slate-800 mb-1">同行网络</h2>
              <p className="text-xs text-slate-500 mb-5 font-medium">邀请好友加入当前推演空间</p>
              <div className="flex justify-between items-center bg-white rounded-xl p-3.5 border border-slate-200 shadow-sm cursor-copy hover:bg-slate-50 transition-colors">
                <span className="text-xs font-bold text-slate-500">专属房间码</span>
                <span className="text-xl font-black text-orange-600 tracking-wider flex items-center gap-2">
                  {roomCode}
                  <button onClick={() => setRoomCode(Math.random().toString(36).substring(2, 8).toUpperCase())} className="p-1 hover:bg-orange-50 rounded-md transition-colors" title="刷新房间码">
                    <RefreshCw className="w-4 h-4 text-orange-500" />
                  </button>
                </span>
              </div>
            </div>
            <div className="p-6 flex-1 flex flex-col gap-4 overflow-y-auto">
              <MemberSlot name="我" role="主控节点" status="已就绪" isSelf avatarSeed="Felix" />
              <InviteSlot />
              <InviteSlot />
            </div>
          </motion.div>
        );
    }
  };

  if (showWorkspace) {
    const roleMapping: Record<string, string> = {
      'foodie': '寻味探索',
      'photo': '视觉体验',
      'chill': '休闲漫步',
      'hardcore': '深度探索'
    };
    const finalRole = selectedRole ? roleMapping[selectedRole] : '常规游玩';
    
    return (
      <SafeErrorBoundary>
        <UnifiedWorkspace mode={selectedMode} role={finalRole} onBack={() => setShowWorkspace(false)} />
      </SafeErrorBoundary>
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
            className="relative z-10 w-full max-w-[1200px] min-h-[85vh] bg-white/70 backdrop-blur-3xl border border-white shadow-[0_8px_40px_rgba(0,0,0,0.04)] rounded-[2rem] flex flex-col overflow-hidden"
          >
            <header className="px-10 py-6 flex justify-between items-center border-b border-slate-200/50 bg-white/40">
              <div className="flex items-center gap-4">
                <OmniLogo className="w-10 h-10 hover:scale-105 transition-transform" />
                <div>
                  <h1 className="text-2xl font-black text-slate-800 tracking-tight">OmniRoute</h1>
                  <p className="text-xs text-slate-500 font-bold tracking-widest uppercase mt-0.5">多智能体协同旅行中枢</p>
                </div>
              </div>
              <div className="flex items-center gap-5">
                <div className="hidden sm:flex items-center gap-2 px-4 py-1.5 bg-white border border-slate-100 rounded-full text-xs font-bold text-slate-600 shadow-sm">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                  系统引擎已就绪
                </div>
                <div className="w-11 h-11 rounded-full overflow-hidden bg-white border border-slate-200 shadow-sm">
                   <img src="https://api.dicebear.com/9.x/avataaars/svg?seed=Felix&backgroundColor=b6e3f4" alt="avatar" className="w-full h-full object-cover" />
                </div>
              </div>
            </header>

            <main className="flex-1 p-10 grid grid-cols-1 lg:grid-cols-12 gap-10 overflow-y-auto overflow-x-hidden">
              <div className="lg:col-span-8 flex flex-col gap-8">
                <div className="mb-2">
                  <h2 className="text-3xl font-extrabold text-slate-800 mb-3 tracking-tight">开启下一段美好旅程。</h2>
                  <p className="text-slate-500 text-base font-medium">选择出行偏好，OmniRoute 底层智能体将为您精细规划路线，让旅行回归纯粹的享受。</p>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
                  <TravelModeCard id="solo" title="一人行" desc="步调全由自己掌控，为你定制绝对自由的私人路书。" icon={<MapPin className="w-6 h-6" />} color="text-emerald-600" selected={selectedMode === 'solo'} onClick={() => setSelectedMode('solo')} />
                  <TravelModeCard id="coop" title="亲友结伴" desc="告别众口难调，自动调和所有人的喜好与时间冲突。" icon={<Users className="w-6 h-6" />} color="text-orange-600" selected={selectedMode === 'coop'} onClick={() => setSelectedMode('coop')} />
                  <TravelModeCard id="pvp" title="高性价比" desc="精打细算，利用算法为你匹配最优的体验成本比。" icon={<PiggyBank className="w-6 h-6" />} color="text-rose-600" selected={selectedMode === 'pvp'} onClick={() => setSelectedMode('pvp')} />
                </div>

                <div className="mt-4 bg-white/80 backdrop-blur-md rounded-2xl p-7 shadow-sm border border-slate-100 flex items-start gap-5 transition-all">
                  <div className="p-3 bg-orange-50 rounded-xl shrink-0">
                    <Sparkles className="w-6 h-6 text-orange-500" />
                  </div>
                  <div>
                    <h3 className="text-lg font-extrabold text-slate-800 mb-2">
                      {selectedMode === 'solo' && "✨ 专注自我，深度探索"}
                      {selectedMode === 'coop' && '✨ 什么是"多智能体协同"？'}
                      {selectedMode === 'pvp' && "✨ 极致性价比是如何做到的？"}
                    </h3>
                    <p className="text-sm text-slate-600 leading-relaxed font-medium">
                      {selectedMode === 'solo' && "系统将关闭社交与分享冗余功能，为您启动单人专属适应度函数。"}
                      {selectedMode === 'coop' && "在传统的群聊中，排行程总是充满妥协。现在，只需将房间码分享给同行好友，系统内置的多个智能体会倾听每个人的心声。"}
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

            <footer className="p-8 border-t border-slate-200/50 bg-white/30 flex justify-end">
              <button onClick={handleBeginPlan} className="px-10 py-4 bg-orange-500 text-white rounded-2xl font-bold text-sm flex items-center justify-center gap-3 hover:bg-orange-600 shadow-lg shadow-orange-200/50 hover:-translate-y-0.5 transition-all duration-300 cursor-pointer">
                下一步：定制专属体验 <ArrowRight className="w-4 h-4" />
              </button>
            </footer>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showAuthModal && !isTransitioning && (
          <motion.div key="auth-modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-slate-900/20 backdrop-blur-sm z-[100] flex items-center justify-center p-4">
            <motion.div key="auth-modal-dialog" initial={{ scale: 0.95, y: 20 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: -20 }} className="bg-white rounded-3xl w-full max-w-xl p-10 shadow-2xl border border-slate-100">
              <div className="text-center mb-8">
                <h3 className="text-2xl font-extrabold text-slate-800 mb-2">这趟旅程，你最期待什么？</h3>
                <p className="text-sm text-slate-500 font-medium">告诉我们你的核心诉求，智能体矩阵将为你量身定制专属体验。</p>
              </div>
              <div className="grid grid-cols-2 gap-4 mb-10">
                <PreferenceCard id="foodie" icon={<Coffee/>} title="寻味探索" desc="美食驱动，为您匹配当地特色。" selected={selectedRole === 'foodie'} onClick={() => setSelectedRole('foodie')} color="border-orange-500 bg-orange-50/80 text-orange-600" />
                <PreferenceCard id="photo" icon={<Camera/>} title="视觉体验" desc="出片导向，精准匹配摄影光线。" selected={selectedRole === 'photo'} onClick={() => setSelectedRole('photo')} color="border-purple-500 bg-purple-50/80 text-purple-600" />
                <PreferenceCard id="chill" icon={<Car/>} title="休闲漫步" desc="拒绝特种兵，安排宽裕休息时间。" selected={selectedRole === 'chill'} onClick={() => setSelectedRole('chill')} color="border-blue-500 bg-blue-50/80 text-blue-600" />
                <PreferenceCard id="hardcore" icon={<MapIcon/>} title="深度探索" desc="行程紧凑，打卡最多核心地标。" selected={selectedRole === 'hardcore'} onClick={() => setSelectedRole('hardcore')} color="border-emerald-500 bg-emerald-50/80 text-emerald-600" />
              </div>
              <div className="flex gap-4">
                <button onClick={() => !isConnecting && setShowAuthModal(false)} className="flex-1 py-3.5 bg-slate-50 border border-slate-200 text-slate-600 font-bold rounded-xl hover:bg-slate-100 transition-colors cursor-pointer">再想想</button>
                <button onClick={handleConfirmSync} disabled={!selectedRole || isConnecting} className={`flex-1 py-3.5 rounded-xl font-bold flex items-center justify-center gap-2 transition-all cursor-pointer ${!selectedRole ? 'bg-slate-100 text-slate-400' : isConnecting ? 'bg-orange-500 text-white' : 'bg-orange-500 text-white hover:bg-orange-600 shadow-md shadow-orange-200/50'}`}>
                  {isConnecting ? <><RefreshCw className="w-4 h-4 animate-spin"/>激活引擎...</> : <>进入共识沙盘 <ArrowRight className="w-4 h-4" /></>}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ================= 工作区区域 =================
function UnifiedWorkspace({ mode, role, onBack }: { mode: string, role: string, onBack: () => void }) {
  const [phase, setPhase] = useState<Phase>('drafting');
  const [intentsReady, setIntentsReady] = useState(false);
  const [showBlackboard, setShowBlackboard] = useState(false);
  
  const [selectedPoiIndex, setSelectedPoiIndex] = useState<number | null>(0);
  const [activeDayIndex, setActiveDayIndex] = useState<number>(0);

  const [userIntent, setUserIntent] = useState('');
  const [dynamicRoutes, setDynamicRoutes] = useState<any[]>([]);
  const [realPath, setRealPath] = useState<[number, number][]>([]);
  const [targetCityInfo, setTargetCityInfo] = useState<{name: string, lnglat: [number, number]} | null>(null);

  const [weatherInfo, setWeatherInfo] = useState<any>(null);
  const [trafficInfo, setTrafficInfo] = useState<any>(null);
  const [travelDetails, setTravelDetails] = useState<Record<string, any>>({});
  
  // 👑 全动态预算拆解数据状态
  const [budgetData, setBudgetData] = useState<any>(null);

  const [streamedText, setStreamedText] = useState(""); 
  const [consensusSummary, setConsensusSummary] = useState('');
  const [apiError, setApiError] = useState<string | null>(null);

  const [pvpPriceIntel, setPvpPriceIntel] = useState<string>("");
  const [pvpPriceLoading, setPvpPriceLoading] = useState<boolean>(true);

  const [incrementalHistory, setIncrementalHistory] = useState<string[]>([]);

  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    const connect = () => {
      if (!isMounted) return;
      console.log("🟡 [WS 协同网关] 尝试连接...");
      ws = new WebSocket(`ws://localhost:8080/ws?room_id=room_omni_001&user_id=user_master`);
      
      ws.onopen = () => {
        console.log("🟢 [WS 协同网关] 连接成功");
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
                
                // 1. 尝试全量 JSON 解析
                const finalData = tryExtractJson(newText);
                if (finalData && finalData.route && Array.isArray(finalData.route) && finalData.route.length > 0) {
                  const mappedRoutes = finalData.route.map((r: any) => ({
                    day: r.day || 1,
                    name: r.location,
                    lnglat: r.lnglat,
                    color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6", 
                    desc: r.desc || r.action,
                    time: r.time,             
                    time_reason: r.time_reason || "",
                    transport: r.transport,   
                    tags: r.tags || [],             
                    cost: r.cost_estimate,
                    photos: r.photos || [],
                    trust_reason: r.trust_reason || "核心地标推荐",
                    amap_url: r.amap_url || "",
                    hotel_candidates: r.hotel_candidates || []
                  }));
                  
                  setDynamicRoutes(mappedRoutes);
                  if (finalData.negotiation_summary) setConsensusSummary(finalData.negotiation_summary);
                  setSelectedPoiIndex(0);
                  setActiveDayIndex(0);
                  setPhase('decision');
                } else {
                  // 👑 2. 秒级切屏核心修复：增量流式提取！只要吐出单条 POI 对象（2秒内），立刻秒级切屏上屏，决不卡死！
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
            } else if (msg.actual_path) {
               setRealPath(msg.actual_path);
            }
          }
        } catch (err) {}
      };

      ws.onerror = () => setApiError("协同网络中断，请检查 Go 网关是否正常运行。");
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
  }, []);

  const humanReadableLogs = useMemo(() => {
    if (!streamedText) return "智能体空间思考中...";
    
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
    return cleaned || "[系统中枢]: 多智能体正在分析全域 POI 节点与最佳时空图论，生成定制路书中...";
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

    const wsPayload = {
      type: "agent_negotiate",
      payload: {
        destinations: [],
        user_preferences: {
          mode: mode,
          role: role, 
          intent: userIntent,
          history_sequence: updatedHistory,
          current_existing_route: dynamicRoutes
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

  // 👑 彻底解决切城市问题：强行锁定当前目标城市名，绝对不准跳变成成都！
  const handleSinglePoiSwap = (poiName: string) => {
    const currentCityName = targetCityInfo?.name || '北京';
    const swapPrompt = `保持在【${currentCityName}】不变，仅把行程中的景点【${poiName}】平替换成【${currentCityName}】另一个同等知名度的高评分景点或地标，绝对不要改变城市和其它天数的规划。`;
    setUserIntent(swapPrompt);
    handleSubmitIntent();
  };

  return (
    <motion.div key="workspace-root" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} className="h-screen w-full bg-white flex overflow-hidden font-sans text-slate-800">
      
      {/* 黄金比例：左侧长图文游记与路径排版区 */}
      <div className="w-[620px] xl:w-[700px] flex flex-col relative z-20 border-r border-slate-200 bg-white shadow-2xl shrink-0">
        <header className="px-8 py-5 border-b border-slate-100 flex justify-between items-center bg-white shadow-xs z-10">
          <div>
            <h2 className="text-2xl font-black text-slate-800 tracking-tight">{targetCityInfo ? targetCityInfo.name + '全景路线规划' : '规划您的行程'}</h2>
            <p className="text-xs text-slate-400 font-bold mt-0.5">OmniRoute 多智能体联网知识增强交付</p>
          </div>
          {phase === 'decision' && (
            <button onClick={() => { setPhase('drafting'); setIntentsReady(false); }} className="px-3.5 py-1.5 bg-orange-50 text-orange-600 rounded-xl font-bold text-xs hover:bg-orange-100 transition-colors flex items-center gap-1.5 border border-orange-100 shadow-2xs cursor-pointer">
              <Wand2 className="w-3.5 h-3.5"/> 重新调整
            </button>
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
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* =============== 右侧：地图与全视图渲染 =============== */}
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
        />

        <div className="absolute top-6 right-6 z-40 flex items-center gap-3 pointer-events-auto">
          <button 
            onClick={() => setShowBlackboard(!showBlackboard)} 
            className="bg-white/90 backdrop-blur-md p-2.5 rounded-xl shadow-lg text-slate-700 hover:text-orange-500 border border-slate-200/80 transition-all hover:scale-105 cursor-pointer"
            title="查看智能体推演黑板"
          >
            <MessageSquare className="w-4 h-4"/>
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
// 大厅与工具组件
// -------------------------------------------------------------------------
function TravelModeCard({ title, desc, icon, color, selected, onClick }: any) {
  return (
    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} onClick={onClick} className={`cursor-pointer p-6 rounded-2xl border transition-all duration-300 flex flex-col group ${selected ? 'border-orange-500 bg-orange-50/60 shadow-md' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
      <div className={`mb-5 w-12 h-12 rounded-xl flex items-center justify-center transition-transform ${selected ? 'bg-orange-500 text-white shadow-sm' : 'bg-slate-100 text-slate-500 group-hover:scale-110'}`}>
        <div className={selected ? 'text-white' : color}>{icon}</div>
      </div>
      <div>
        <h3 className="text-lg font-extrabold text-slate-800 mb-2">{title}</h3>
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

function MemberSlot({ name, role, status, isSelf, avatarSeed }: any) {
  return (
    <div className={`p-3 rounded-xl flex items-center justify-between border ${isSelf ? 'bg-orange-50/80 border-orange-200 shadow-sm' : 'bg-white border-slate-100'}`}>
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-white rounded-full overflow-hidden border border-slate-200">
          <img src={`https://api.dicebear.com/9.x/avataaars/svg?seed=${avatarSeed}&backgroundColor=b6e3f4`} alt="avatar" />
        </div>
        <div>
          <p className="text-sm font-bold text-slate-800">{name}</p>
          <p className="text-[10px] text-slate-500">{role}</p>
        </div>
      </div>
      <span className="text-[10px] font-bold px-2 py-1 bg-white rounded-md text-slate-600 border border-slate-100">{status}</span>
    </div>
  );
}

function InviteSlot() {
  return (
    <button className="p-3 rounded-xl border-2 border-dashed border-slate-200 bg-slate-50/50 flex items-center gap-3 text-slate-400 hover:text-orange-500 hover:bg-slate-50 transition-all cursor-pointer">
      <div className="w-10 h-10 rounded-full bg-white flex items-center justify-center border border-slate-200 shadow-sm"><UserPlus className="w-4 h-4" /></div>
      <span className="text-xs font-bold">虚位以待...</span>
    </button>
  );
}

function DraftingPanel({ mode, onSubmit, isReady, userIntent, setUserIntent, apiError, historyLength }: any) {
  return (
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="p-8 flex flex-col h-full relative">
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-orange-50/90 to-amber-50/60 border border-orange-100 p-5 shadow-xs mb-6">
        <h3 className="text-sm font-extrabold text-orange-900 mb-2 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-orange-500" />
          {historyLength > 0 ? `多轮对齐模式 (已追加 ${historyLength} 次诉求)` : '专属向导已就绪'}
        </h3>
        <p className="text-xs text-orange-700/80 leading-relaxed font-medium">
          {historyLength > 0 ? '支持增量精进！您可以直接输入："把第一天的路线缩短"、"中午想吃火锅"等。系统会在当前成果上打差量补丁。' : '我是你的专属路线向导。请随性写下你对这趟旅程的想象（支持模糊意图），底层的智能体也会为你补全细节。'}
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
            {isReady && <span className="text-[10px] bg-emerald-100 text-emerald-600 px-2.5 py-1 rounded-full font-bold flex items-center gap-1"><Check className="w-3 h-3"/> 已提交推演</span>}
          </div>
          
          <textarea 
            disabled={isReady}
            value={userIntent}
            onChange={(e) => setUserIntent(e.target.value)}
            className="w-full flex-1 bg-transparent resize-none outline-none text-sm text-slate-700 placeholder:text-slate-300 font-medium leading-relaxed custom-scrollbar"
            placeholder={historyLength > 0 ? "例如：还是去那儿，但是把第二天的午餐平替成便宜一点的老字号小吃..." : "例如：我打算去徐州玩2天，希望能深入体验特色美食，预算有限..."}
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
            <><RefreshCw className="w-4 h-4 animate-spin" /> 智能体增量差量精进中...</>
          ) : (
            <>{historyLength > 0 ? "追加诉求并迭代路书" : "锁定意图并开始推演"} <ArrowRight className="w-4 h-4" /></>
          )}
        </button>
      </div>
    </motion.div>
  );
}

// 👑 加载等待面板：显示动态推演日志，避免死等发呆
function DeductionPanel({ latestLog }: { latestLog?: string }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center py-20 h-full text-center px-8">
      <div className="relative w-24 h-24 mb-6">
        <motion.div animate={{ rotate: 360 }} transition={{ duration: 3, repeat: Infinity, ease: "linear" }} className="absolute inset-0 border-2 border-dashed border-orange-400 rounded-full" />
        <div className="absolute inset-0 flex items-center justify-center">
          <RefreshCw className="w-6 h-6 text-orange-500 animate-spin" />
        </div>
      </div>
      <h3 className="text-base font-black text-slate-800">正在协同寻优空间拓扑...</h3>
      
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

// 👑 全动态预算展示卡片
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

// 👑 展开式交通分步导航连接器：全多模态 (🚗驾车 / 🚌公交地铁 / 🚶步行) 选项 Tab 切换
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
  const durMin = currentModeData?.duration_min || 15;
  const distKm = currentModeData?.distance_km || 3.5;

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
          <span className="font-bold text-slate-700 flex items-center gap-1">
            <Car className="w-3.5 h-3.5 text-orange-500 group-hover:scale-110 transition-transform"/> {label}
          </span>
          <span className="text-slate-300">·</span>
          <span className="font-bold text-slate-600">约 {durMin} 分钟 ({distKm}km)</span>
        </div>

        <div className="flex items-center gap-1 text-[11px] font-bold text-orange-500">
          <span>{isOpen ? '收起路线' : '展开多模态高德逐字导航'}</span>
          {isOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </div>
      </button>

      {/* 👑 多模态 (驾车 / 公交地铁 / 步行) 切换 Tab 与高德指引 */}
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
              <div className="text-[11px] text-slate-300 leading-relaxed space-y-1.5 pt-1">
                <div>1. 从【{pt.name}】出发进入主要道路；</div>
                <div>2. 沿主干线出行约 {durMin} 分钟 ({distKm}km)；</div>
                <div>3. 抵达目的地【{nextPt.name}】入口。</div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// 👑 马蜂窝风格长图文路线面板 (含高德实景超链接 + 建议时段理由 + 备选酒店 Tab 选项 + 🔄 换一换)
function MafengwoStylePanel({ routes, totalDays, activeDayIndex, onSelectDay, travelDetails, selectedPoiIndex, onSelectPoi, weatherInfo, trafficInfo, onReset, consensusSummary, budgetData, onSwapPoi }: any) {
  const [showWeatherForecast, setShowWeatherForecast] = useState(false);

  return (
    <div className="pt-2 pb-32 font-sans">
      {/* 1. 真实可点击切换的天数 Filter Pills */}
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
        {/* 👑 全动态预算拆解卡片 */}
        <DynamicBudgetCard summary={consensusSummary} budgetData={budgetData} />

        {/* 2. 全域天气预警卡片 */}
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
          {/* 贯穿式垂直时间轴干线 */}
          <div className="absolute top-3 bottom-6 left-[11px] w-[2px] bg-slate-200/80"></div>

          {Array.isArray(routes) && routes.map((pt: any, idx: number) => {
            const isSelected = selectedPoiIndex === idx;

            // 👑 智能实景图离散化：通过 (idx + i) 散列生成 3 张完全不同的景观图，彻底解决图片重合！
            const realPhotos = (pt.photos && pt.photos.length > 0)
              ? pt.photos.map((url: string, i: number) => getCleanPhotoUrl(url, pt.name, idx + i))
              : [
                  getCleanPhotoUrl(undefined, pt.name, idx + 0),
                  getCleanPhotoUrl(undefined, pt.name, idx + 1),
                  getCleanPhotoUrl(undefined, pt.name, idx + 2)
                ];

            return (
              <div key={`poi-node-card-${pt.name}-${idx}`} className="relative mb-10">
                
                {/* 1. 节点序号 */}
                <div 
                  onClick={() => onSelectPoi(idx)}
                  className={`absolute -left-[30px] top-0.5 w-7 h-7 rounded-full flex items-center justify-center text-[12px] font-black cursor-pointer transition-all z-10 ${isSelected ? 'bg-orange-500 text-white shadow-lg shadow-orange-300 ring-4 ring-orange-100 scale-110' : 'bg-white text-slate-500 border-2 border-slate-300 hover:border-orange-400'}`}
                >
                  {idx + 1}
                </div>

                {/* 2. 景点信息与真实照片画廊 */}
                <div className="cursor-pointer group" onClick={() => onSelectPoi(idx)}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-3">
                      <h3 className={`text-xl font-black transition-colors ${isSelected ? 'text-orange-500' : 'text-slate-800 group-hover:text-orange-500'}`}>{pt.name}</h3>
                      <span className="flex items-center gap-1 bg-orange-50 text-orange-600 px-2 py-0.5 rounded text-xs font-bold border border-orange-100">
                        ⭐ 4.8
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      {/* 👑 核心新特性：单节点“🔄 换一换”单点平替按钮 */}
                      {onSwapPoi && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onSwapPoi(pt.name); }}
                          className="text-xs text-slate-500 hover:text-orange-600 font-bold flex items-center gap-1 bg-slate-100 hover:bg-orange-50 px-2.5 py-1 rounded-lg border border-slate-200 hover:border-orange-200 transition-colors cursor-pointer"
                          title="不满意这个地点？一键换成同类好去处"
                        >
                          <RotateCw className="w-3 h-3" />
                          <span>换一换</span>
                        </button>
                      )}

                      {/* 👑 高德地图实景直达超链接按钮 */}
                      {pt.amap_url && (
                        <a 
                          href={pt.amap_url} 
                          target="_blank" 
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="text-xs text-orange-600 hover:text-orange-700 font-bold flex items-center gap-1 bg-orange-50/80 px-2.5 py-1 rounded-lg border border-orange-200/80 hover:bg-orange-100 transition-colors"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                          <span>高德外链</span>
                        </a>
                      )}
                    </div>
                  </div>

                  {/* 👑 建议时段 Badge */}
                  {pt.time && (
                    <div className="mb-3 inline-flex items-center gap-1.5 text-xs font-bold text-orange-600 bg-orange-50 px-3 py-1 rounded-xl border border-orange-200/80 shadow-2xs">
                      <Clock className="w-3.5 h-3.5 text-orange-500" />
                      <span>建议游玩时段：{pt.time}</span>
                    </div>
                  )}

                  {/* 马蜂窝 3 图差异化画廊 */}
                  <div className="grid grid-cols-3 gap-2 mb-4">
                    {realPhotos.slice(0, 3).map((src: string, imgIdx: number) => (
                      <div key={`img-thumb-${idx}-${imgIdx}`} className="rounded-xl overflow-hidden aspect-[4/3] bg-slate-100 border border-slate-100 shadow-2xs">
                         <img src={src} alt={`${pt.name}-${imgIdx}`} className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105" />
                      </div>
                    ))}
                  </div>

                  {/* 详细文案描述 */}
                  <div className="text-sm text-slate-600 leading-relaxed space-y-2.5 mb-4">
                    <p className="leading-loose"><span className="font-bold text-slate-800">体验与文化：</span>{pt.desc}</p>
                    
                    {/* 👑 最佳时段理由卡片 */}
                    {pt.time_reason && (
                      <p className="text-xs bg-sky-50/80 p-2.5 rounded-xl border border-sky-100 text-sky-800 leading-relaxed">
                        <span className="font-bold text-sky-600">🕒 最佳时段理由：</span>{pt.time_reason}
                      </p>
                    )}

                    {pt.trust_reason && <p className="text-xs bg-slate-50 p-2.5 rounded-lg border border-slate-100 text-slate-500"><span className="font-bold text-orange-600">💡 推荐依据：</span>{pt.trust_reason}</p>}
                    
                    {/* 特色标签 */}
                    {pt.tags && pt.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {pt.tags.map((tag: string, tIdx: number) => <span key={`tag-pill-${idx}-${tIdx}`} className="bg-orange-50/80 border border-orange-100 text-orange-600 text-[11px] px-2 py-0.5 rounded-md font-bold">{tag}</span>)}
                      </div>
                    )}
                  </div>

                  {/* 👑 备选酒店同商圈置换卡片 (带详情外链 & 明确置换按钮) */}
                  {pt.hotel_candidates && pt.hotel_candidates.length > 0 && (
                    <div className="mt-3 p-3.5 rounded-2xl bg-purple-50/80 border border-purple-200/80 text-xs">
                      <div className="font-bold text-purple-900 mb-2 flex items-center justify-between">
                        <span className="flex items-center gap-1.5"><Hotel className="w-4 h-4 text-purple-600" /> 同商圈备选品质酒店（先查看详情再决定是否置换）：</span>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        {pt.hotel_candidates.map((cand: any, cIdx: number) => {
                          const candName = typeof cand === 'string' ? cand : cand.name;
                          const candPrice = typeof cand === 'object' ? cand.price : '';
                          const candUrl = typeof cand === 'object' ? cand.amap_url : `https://www.amap.com/search?query=${candName}`;
                          return (
                            <div key={cIdx} className="bg-white p-2.5 rounded-xl border border-purple-100 flex items-center justify-between shadow-2xs">
                              <span className="font-bold text-slate-800 text-xs truncate max-w-[130px]">{candName} {candPrice && <span className="text-purple-600 font-normal">({candPrice})</span>}</span>
                              <div className="flex items-center gap-1.5 shrink-0">
                                <a 
                                  href={candUrl} 
                                  target="_blank" 
                                  rel="noopener noreferrer" 
                                  onClick={(e) => e.stopPropagation()}
                                  className="px-2 py-1 text-purple-600 hover:bg-purple-50 rounded-md border border-purple-200 text-[11px] font-bold flex items-center gap-0.5 cursor-pointer"
                                >
                                  <ExternalLink className="w-3 h-3" /> 详情
                                </a>
                                <button 
                                  onClick={(e) => { e.stopPropagation(); onSwapPoi(pt.name); }} 
                                  className="px-2 py-1 bg-purple-600 hover:bg-purple-700 text-white rounded-md text-[11px] font-bold flex items-center gap-0.5 cursor-pointer"
                                >
                                  <RotateCw className="w-3 h-3" /> 置换
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>

                {/* 3. 完美下钻可展开的高德逐字导航指引 */}
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

        {/* 增量调优按钮 */}
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

// 👑 地图底部贴地真实照片轮播 (点击照片联动定位地图与左侧列表)
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
          const photoUrl = (r.photos && r.photos.length > 0) 
            ? getCleanPhotoUrl(r.photos[0], r.name, idx) 
            : getCleanPhotoUrl(undefined, r.name, idx);

          return (
            <div
              key={`carousel-card-item-${r.name}-${idx}`}
              onClick={() => onSelect(idx)}
              className={`snap-center shrink-0 w-[200px] h-[120px] rounded-2xl overflow-hidden relative cursor-pointer transition-all duration-300 transform bg-white ${isSelected ? 'ring-4 ring-orange-500 ring-offset-2 ring-offset-slate-900 scale-105 shadow-2xl z-10' : 'opacity-85 hover:opacity-100 hover:scale-102 shadow-md'}`}
            >
              <img src={photoUrl} className="absolute inset-0 w-full h-full object-cover" alt={r.name} />
              
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