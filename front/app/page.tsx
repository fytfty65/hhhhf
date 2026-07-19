'use client';

import dynamic from 'next/dynamic';
import React, { useState, useRef, useEffect } from 'react';
import { 
  Users, MapPin, Sparkles, Coffee, Camera, Car, PiggyBank, ArrowRight, Check, 
  UserPlus, Map as MapIcon, Compass, Headphones, TrendingDown, RefreshCw, 
  X, Play, AlertCircle, Clock, ThumbsUp, BrainCircuit,
  MessageSquare, Wand2, History
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const InteractiveAmapComponent = dynamic(
  () => import('./InteractiveAmapComponent'),
  {
    ssr: false,
    loading: () => (
      <div className="absolute inset-0 w-full h-full bg-slate-50 flex flex-col items-center justify-center gap-3">
        <RefreshCw className="w-6 h-6 text-blue-400 animate-spin" />
        <span className="text-sm font-bold text-slate-400 tracking-wider">空间拓扑引擎装载中...</span>
      </div>
    )
  }
);

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
              className="px-8 py-3 bg-slate-900 text-white rounded-xl font-bold text-sm hover:bg-blue-600 transition-all shadow-md"
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
    }, 1500); 
  };

  const renderRightPanel = () => {
    switch (selectedMode) {
      case 'solo':
        return (
          <motion.div key="solo" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.3 }} className="flex flex-col h-full">
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
          <motion.div key="pvp" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.3 }} className="flex flex-col h-full">
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
          <motion.div key="coop" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.3 }} className="flex flex-col h-full">
            <div className="p-7 pb-5 border-b border-slate-200/60 bg-white/50">
              <h2 className="text-xl font-bold text-slate-800 mb-1">同行网络</h2>
              <p className="text-xs text-slate-500 mb-5 font-medium">邀请好友加入当前推演空间</p>
              <div className="flex justify-between items-center bg-white rounded-xl p-3.5 border border-slate-200 shadow-sm cursor-copy hover:bg-slate-50 transition-colors">
                <span className="text-xs font-bold text-slate-500">专属房间码</span>
                <span className="text-xl font-black text-blue-600 tracking-wider flex items-center gap-2">
                  {roomCode}
                  <button onClick={() => setRoomCode(Math.random().toString(36).substring(2, 8).toUpperCase())} className="p-1 hover:bg-blue-50 rounded-md transition-colors" title="刷新房间码">
                    <RefreshCw className="w-4 h-4 text-blue-500" />
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
    
    return <SafeErrorBoundary><UnifiedWorkspace mode={selectedMode} role={finalRole} onBack={() => setShowWorkspace(false)} /></SafeErrorBoundary>;
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 sm:p-8 font-sans bg-slate-50/50 relative overflow-hidden">
      <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] rounded-full bg-blue-400/20 blur-[120px] pointer-events-none"></div>
      <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] rounded-full bg-emerald-400/20 blur-[120px] pointer-events-none"></div>

      <AnimatePresence>
        {!isTransitioning && (
          <motion.div 
            initial={{ opacity: 0, scale: 0.98, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95, y: -20, filter: "blur(8px)" }} transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className="relative z-10 w-full max-w-[1200px] min-h-[85vh] bg-white/60 backdrop-blur-3xl border border-white shadow-[0_8px_40px_rgba(0,0,0,0.04)] rounded-[2rem] flex flex-col overflow-hidden"
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
                   <img src="https://api.dicebear.com/7.x/notionists/svg?seed=Felix&backgroundColor=transparent" alt="avatar" className="w-full h-full object-cover" />
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
                  <TravelModeCard id="coop" title="亲友结伴" desc="告别众口难调，自动调和所有人的喜好与时间冲突。" icon={<Users className="w-6 h-6" />} color="text-blue-600" selected={selectedMode === 'coop'} onClick={() => setSelectedMode('coop')} />
                  <TravelModeCard id="pvp" title="高性价比" desc="精打细算，利用算法为你匹配最优的体验成本比。" icon={<PiggyBank className="w-6 h-6" />} color="text-rose-600" selected={selectedMode === 'pvp'} onClick={() => setSelectedMode('pvp')} />
                </div>

                <div className="mt-4 bg-white/80 backdrop-blur-md rounded-2xl p-7 shadow-sm border border-slate-100 flex items-start gap-5 transition-all">
                  <div className="p-3 bg-blue-50 rounded-xl shrink-0">
                    <Sparkles className="w-6 h-6 text-blue-600" />
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
              <button onClick={handleBeginPlan} className="px-10 py-4 bg-slate-900 text-white rounded-2xl font-bold text-sm flex items-center justify-center gap-3 hover:bg-slate-800 shadow-lg hover:shadow-xl hover:-translate-y-0.5 transition-all duration-300">
                下一步：定制专属体验 <ArrowRight className="w-4 h-4" />
              </button>
            </footer>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showAuthModal && !isTransitioning && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-slate-900/20 backdrop-blur-sm z-[100] flex items-center justify-center p-4">
            <motion.div initial={{ scale: 0.95, y: 20 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, y: -20 }} className="bg-white rounded-3xl w-full max-w-xl p-10 shadow-2xl border border-slate-100">
              <div className="text-center mb-8">
                <h3 className="text-2xl font-extrabold text-slate-800 mb-2">这趟旅程，你最期待什么？</h3>
                <p className="text-sm text-slate-500 font-medium">告诉我们你的核心诉求，智能体矩阵将为你量身定制专属体验。</p>
              </div>
              <div className="grid grid-cols-2 gap-4 mb-10">
                <PreferenceCard id="foodie" icon={<Coffee/>} title="寻味探索" desc="美食驱动，为您匹配当地特色。" selected={selectedRole === 'foodie'} onClick={() => setSelectedRole('foodie')} color="border-orange-200 bg-orange-50 text-orange-600" />
                <PreferenceCard id="photo" icon={<Camera/>} title="视觉体验" desc="出片导向，精准匹配摄影光线。" selected={selectedRole === 'photo'} onClick={() => setSelectedRole('photo')} color="border-purple-200 bg-purple-50 text-purple-600" />
                <PreferenceCard id="chill" icon={<Car/>} title="休闲漫步" desc="拒绝特种兵，安排宽裕休息时间。" selected={selectedRole === 'chill'} onClick={() => setSelectedRole('chill')} color="border-blue-200 bg-blue-50 text-blue-600" />
                <PreferenceCard id="hardcore" icon={<MapIcon/>} title="深度探索" desc="行程紧凑，打卡最多核心地标。" selected={selectedRole === 'hardcore'} onClick={() => setSelectedRole('hardcore')} color="border-emerald-200 bg-emerald-50 text-emerald-600" />
              </div>
              <div className="flex gap-4">
                <button onClick={() => !isConnecting && setShowAuthModal(false)} className="flex-1 py-3.5 bg-slate-50 border border-slate-200 text-slate-600 font-bold rounded-xl hover:bg-slate-100 transition-colors">再想想</button>
                <button onClick={handleConfirmSync} disabled={!selectedRole || isConnecting} className={`flex-1 py-3.5 rounded-xl font-bold flex items-center justify-center gap-2 transition-all ${!selectedRole ? 'bg-slate-100 text-slate-400' : isConnecting ? 'bg-blue-500 text-white' : 'bg-blue-600 text-white hover:bg-blue-700 shadow-md hover:shadow-lg'}`}>
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

function UnifiedWorkspace({ mode, role, onBack }: { mode: string, role: string, onBack: () => void }) {
  const [phase, setPhase] = useState<Phase>('drafting');
  const [intentsReady, setIntentsReady] = useState(false);
  const [showBlackboard, setShowBlackboard] = useState(false);
  const [selectedPoiIndex, setSelectedPoiIndex] = useState<number | null>(null);
  
  const [userIntent, setUserIntent] = useState('');
  const [dynamicRoutes, setDynamicRoutes] = useState<any[]>([]);
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
          try {
            msg = JSON.parse(line);
          } catch {
            continue;
          }
        
        if (msg.type === "stream_token") {
          setStreamedText(prev => {
            const newText = prev + msg.payload;
            
            if (newText.includes("[FINAL_JSON]")) {
               try {
                 const parts = newText.split("[FINAL_JSON]");
                 if (parts.length < 2) return newText;
                 const afterMarker = parts[1];
                 const jsonMatch = afterMarker.match(/\{[\s\S]*\}/);
                 if (!jsonMatch) return newText;
                 const jsonStr = jsonMatch[0].replace(/```json/g, "").replace(/```/g, "");
                 const finalData = JSON.parse(jsonStr);
                 
                 if (finalData.route) {
                     const mappedRoutes = finalData.route.map((r: any) => ({
                         name: r.location,
                         lnglat: r.lnglat,
                         color: r.type === "food" || r.tags?.includes("寻味") ? "#f97316" : "#3b82f6", 
                         desc: r.action,
                         time: r.time,             
                         transport: r.transport,   
                         tags: r.tags || [],             
                         cost: r.cost_estimate,
                         trust_reason: r.trust_reason || "核心地标推荐"
                     }));
                     setDynamicRoutes(mappedRoutes);
                     setConsensusSummary(finalData.negotiation_summary || "");
                     setTimeout(() => setPhase('decision'), 800); 
                 }
               } catch (e) {}
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
        }
        }
      } catch (err) {}
    };

    ws.onerror = () => {
      setApiError("协同网络中断，请检查 Go 网关是否正常运行。");
    };

    ws.onclose = () => {
      console.log("🔴 [WS 协同网关] 连接断开");
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
      console.error("WebSocket 发送失败:", e);
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

  return (
    <motion.div initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} className="h-screen w-full bg-slate-50 flex overflow-hidden font-sans text-slate-800">
      <div className="relative flex-1 bg-slate-100 border-r border-slate-200 overflow-hidden flex flex-col">
        <InteractiveAmapComponent 
          phase={phase} 
          selectedPoiIndex={selectedPoiIndex} 
          onPoiSelect={setSelectedPoiIndex} 
          luoyangRoute={dynamicRoutes.length > 0 ? dynamicRoutes : []} 
          onExit={onBack}
          onWakeAgent={() => setShowBlackboard(true)}
        />

        <div className="absolute bottom-10 left-1/2 -translate-x-1/2 z-30 w-full max-w-2xl px-6 pointer-events-none">
          <AnimatePresence>
            {phase === 'decision' && mode === 'solo' && <div className="pointer-events-auto"><TimelineLayer selectedIndex={selectedPoiIndex} onSelect={setSelectedPoiIndex} routes={dynamicRoutes} key="timeline" /></div>}
            {phase === 'decision' && (mode === 'coop' || mode === 'pvp') && <div className="pointer-events-auto"><VotingLayer key="voting" /></div>}
          </AnimatePresence>
        </div>
      </div>

      <div className="w-[450px] bg-white/90 backdrop-blur-xl flex flex-col relative shadow-2xl z-20 border-l border-white">
        <header className="px-8 py-6 border-b border-slate-100 flex justify-between items-center z-10">
          <div>
            <h2 className="text-xl font-black text-slate-800">{mode === 'coop' ? '同行共识组' : '专属定制向导'}</h2>
            <div className="flex items-center gap-2 mt-1">
               <span className={`w-2 h-2 rounded-full ${phase === 'deduction' ? 'bg-amber-500 animate-pulse' : 'bg-emerald-500'}`}></span>
               <p className="text-xs text-slate-400 font-bold uppercase tracking-wider">
                 {phase === 'drafting' ? '阶段 1: 意图对齐' : phase === 'deduction' ? '阶段 2: 动态推演' : '阶段 3: 路线交付'}
               </p>
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-8 bg-gradient-to-b from-white to-slate-50/50">
          <AnimatePresence mode="wait">
            {phase === 'drafting' && (
              <DraftingPanel 
                mode={mode} 
                onSubmit={handleSubmitIntent} 
                isReady={intentsReady} 
                userIntent={userIntent} 
                setUserIntent={setUserIntent} 
                apiError={apiError} 
                historyLength={incrementalHistory.length}
                key="drafting" 
              />
            )}
            {phase === 'deduction' && <DeductionPanel key="deduction" />}
            {phase === 'decision' && (
              <DecisionPanel 
                mode={mode} 
                routes={dynamicRoutes} 
                selectedPoiIndex={selectedPoiIndex} 
                onPoiSelect={setSelectedPoiIndex} 
                onReset={handleResetIntent} 
                key="decision" 
              />
            )}
          </AnimatePresence>
        </div>

        <AnimatePresence>
          {showBlackboard && (
            <motion.div initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }} transition={{ type: "spring", damping: 25 }} className="absolute top-0 right-0 w-[420px] h-full bg-white/95 backdrop-blur-2xl shadow-[-10px_0_40px_rgba(0,0,0,0.05)] z-50 flex flex-col border-l border-slate-100">
              <div className="p-6 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
                <div>
                  <h3 className="text-slate-800 font-bold flex items-center gap-2"><BrainCircuit className="w-5 h-5 text-blue-500"/> 推演黑板 (Reasoning Board)</h3>
                </div>
                <button onClick={() => setShowBlackboard(false)} className="p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg"><X className="w-5 h-5"/></button>
              </div>
              
              <div className="flex-1 overflow-y-auto p-6 bg-slate-50 font-mono text-slate-700 text-sm shadow-inner custom-scrollbar relative border-t border-slate-200">
                 <pre className="whitespace-pre-wrap leading-relaxed tracking-wide font-medium">
                    {streamedText.split("[FINAL_JSON]")[0]}
                 </pre>
                 {phase === 'deduction' && <span className="inline-block w-2 h-4 bg-blue-500 animate-pulse ml-1 align-middle"></span>}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}

function TravelModeCard({ title, desc, icon, color, selected, onClick }: any) {
  return (
    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} onClick={onClick} className={`cursor-pointer p-6 rounded-2xl border transition-all duration-300 flex flex-col group ${selected ? 'border-blue-400 bg-blue-50/50 shadow-md' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
      <div className={`mb-5 w-12 h-12 rounded-xl flex items-center justify-center transition-transform ${selected ? 'bg-white shadow-sm border border-blue-100' : 'bg-slate-100 text-slate-500 group-hover:scale-110'}`}>
        <div className={selected ? color : ''}>{icon}</div>
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
      {selected && <div className="absolute top-3 right-3"><Check className="w-4 h-4" /></div>}
      <div className={`mb-2 ${selected ? '' : 'text-slate-400'}`}>{React.cloneElement(icon, { className: 'w-5 h-5' })}</div>
      <h4 className={`text-sm font-extrabold mb-1 ${selected ? 'text-slate-900' : 'text-slate-700'}`}>{title}</h4>
      <p className={`text-[11px] font-medium leading-snug ${selected ? 'text-slate-700' : 'text-slate-500'}`}>{desc}</p>
    </motion.div>
  );
}

function MemberSlot({ name, role, status, isSelf, avatarSeed }: any) {
  return (
    <div className={`p-3 rounded-xl flex items-center justify-between border ${isSelf ? 'bg-blue-50 border-blue-200 shadow-sm' : 'bg-white border-slate-100'}`}>
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 bg-white rounded-full overflow-hidden border border-slate-200">
          <img src={`https://api.dicebear.com/7.x/notionists/svg?seed=${avatarSeed}&backgroundColor=transparent`} alt="avatar" />
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
    <button className="p-3 rounded-xl border-2 border-dashed border-slate-200 bg-slate-50 flex items-center gap-3 text-slate-400 hover:text-blue-500">
      <div className="w-10 h-10 rounded-full bg-white flex items-center justify-center border border-slate-200 shadow-sm"><UserPlus className="w-4 h-4" /></div>
      <span className="text-xs font-bold">虚位以待...</span>
    </button>
  );
}

function DraftingPanel({ mode, onSubmit, isReady, userIntent, setUserIntent, apiError, historyLength }: any) {
  return (
    <motion.div initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} className="flex flex-col h-full relative">
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-blue-50/80 to-indigo-50/50 border border-blue-100 p-5 shadow-sm mb-6">
        <h3 className="text-sm font-extrabold text-blue-900 mb-2 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-blue-500" />
          {historyLength > 0 ? `多轮对齐模式 (已追加 ${historyLength} 次诉求)` : '专属向导已就绪'}
        </h3>
        <p className="text-xs text-blue-700/80 leading-relaxed font-medium">
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
        <div className={`flex-1 min-h-[220px] p-5 bg-white rounded-2xl shadow-[0_4px_20px_rgba(0,0,0,0.03)] border transition-all duration-300 flex flex-col group relative ${isReady ? 'border-emerald-200 bg-emerald-50/30' : 'border-slate-200 focus-within:border-blue-400 focus-within:shadow-xl focus-within:ring-4 focus-within:ring-blue-50'}`}>
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
            placeholder={historyLength > 0 ? "例如：还是去那儿，但是把第二天的午餐平替成便宜一点的老字号小吃..." : "例如：我打算去成都玩4天，希望能深入体验特色美食，预算有限，同行有一位老人..."}
          ></textarea>
        </div>
      </div>

      <div className="mt-auto pt-4">
        <button 
          onClick={onSubmit} 
          disabled={isReady || userIntent.trim().length === 0}
          className={`w-full py-4 rounded-2xl font-bold text-sm flex items-center justify-center gap-2 transition-all duration-300 shadow-lg ${isReady ? 'bg-emerald-500 text-white shadow-emerald-200' : userIntent.trim() ? 'bg-slate-900 text-white hover:bg-blue-600 hover:shadow-blue-200 hover:-translate-y-0.5' : 'bg-slate-200 text-slate-400 cursor-not-allowed'}`}
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

function DeductionPanel() {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center py-20 h-full">
      <div className="relative w-24 h-24 mb-6">
        <motion.div animate={{ rotate: 360 }} transition={{ duration: 4, repeat: Infinity, ease: "linear" }} className="absolute inset-0 border-2 border-dashed border-blue-400 rounded-full" />
        <div className="absolute inset-0 flex items-center justify-center"><RefreshCw className="w-6 h-6 text-blue-500 animate-spin" /></div>
      </div>
      <h3 className="text-base font-black text-slate-800">正在协同寻优空间拓扑...</h3>
      <p className="text-xs text-slate-400 mt-2 text-center max-w-xs leading-relaxed">全息雷达数据已捕获。多智能体正在计算天气拥堵权重并覆写静态时间轴。</p>
    </motion.div>
  );
}

function DecisionPanel({ mode, routes, selectedPoiIndex, onPoiSelect, onReset }: any) {
  return (
    <motion.div initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} className="space-y-6 pb-48"> 
      <div className="p-6 bg-gradient-to-br from-blue-50/50 to-emerald-50/30 border border-slate-100 rounded-3xl shadow-sm">
        <h3 className="text-base font-black text-slate-800 flex items-center gap-2"><Sparkles className="w-4 h-4 text-blue-500"/> 专属共识路书已交付</h3>
        <p className="text-sm text-slate-600 leading-relaxed mt-2 font-medium">
          已整合多轮意图。时间轴已根据路况自适应错峰计算。点击节点查看理由。
        </p>
      </div>

      <div className="relative pl-4 border-l-2 border-slate-100 space-y-6">
        {Array.isArray(routes) && routes.map((pt: any, idx: number) => (
          <div 
            key={idx} 
            onClick={() => onPoiSelect(idx)}
            className={`relative p-5 rounded-2xl border transition-all cursor-pointer ${selectedPoiIndex === idx ? 'bg-blue-50/40 border-blue-400 shadow-md scale-[1.02]' : 'bg-white border-slate-100 hover:border-slate-200 shadow-sm'}`}
          >
            <div className={`absolute -left-[23px] top-5 w-2 h-2 rounded-full ${selectedPoiIndex === idx ? 'bg-blue-600 ring-4 ring-blue-100' : 'bg-slate-300'}`} />
            
            <div className="flex justify-between items-start mb-2">
              <h4 className="text-base font-black text-slate-800 tracking-tight">{pt.name}</h4>
              {pt.time && <span className="text-xs font-bold text-slate-600 bg-slate-100 px-2 py-0.5 rounded-full">{pt.time}</span>}
            </div>
            
            <p className="text-sm text-slate-600 leading-relaxed mb-2 font-medium">{pt.desc}</p>
            
            <div className="mb-4 text-xs bg-slate-50 text-slate-500 p-2.5 rounded-xl border border-slate-100/60 font-medium">
               <span className="font-extrabold text-blue-600">💡 推荐依据:</span> {pt.trust_reason}
            </div>
            
            <div className="flex flex-wrap items-center gap-2">
              {pt.transport && (
                <span className="text-xs bg-blue-50 text-blue-600 px-2.5 py-1 rounded-lg font-bold border border-blue-100 flex items-center gap-1.5">
                  <Car className="w-3.5 h-3.5"/> {pt.transport}
                </span>
              )}
              {pt.cost && (
                <span className="text-xs bg-rose-50 text-rose-600 px-2.5 py-1 rounded-lg font-bold border border-rose-100 flex items-center gap-1.5">
                  <PiggyBank className="w-3.5 h-3.5"/> {pt.cost}
                </span>
              )}
              {pt.tags && pt.tags.map((tag: string) => (
                <span key={tag} className="text-xs bg-orange-50 text-orange-600 px-2.5 py-1 rounded-lg font-bold border border-orange-100">
                  {tag}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-8 pt-6 border-t border-slate-100 flex justify-center">
        <button 
          onClick={onReset} 
          className="px-6 py-3 bg-slate-900 border border-slate-800 text-white rounded-xl font-bold text-sm flex items-center gap-2 hover:bg-blue-600 transition-all shadow-md"
        >
          <Wand2 className="w-4 h-4" /> 对当前路线增量调优
        </button>
      </div>
    </motion.div>
  );
}

function TimelineLayer({ selectedIndex, onSelect, routes }: any) {
  const nodes = routes && routes.length > 0 ? routes.map((r: any) => r.name.split(' ')[0].substring(0, 5)) : ["起始点"];
  return (
    <motion.div initial={{ y: 30, opacity: 0 }} animate={{ y: 0, opacity: 1 }} className="bg-white/90 backdrop-blur-xl border border-slate-200/80 p-5 rounded-3xl shadow-xl w-full flex items-center gap-6 pointer-events-auto">
      <button className="p-3 bg-slate-900 text-white rounded-full shadow-md hover:bg-slate-800 transition-colors"><Play className="w-4 h-4 fill-current ml-0.5"/></button>
      <div className="flex-1 overflow-hidden">
        <div className="flex justify-between text-[10px] font-bold text-slate-400 mb-3 px-1 min-w-max gap-4 overflow-x-auto custom-scrollbar">
          {nodes.map((node: string, i: number) => (
            <span key={i} onClick={() => onSelect(i)} className={`cursor-pointer transition-colors whitespace-nowrap ${selectedIndex === i ? 'text-blue-600 font-black' : 'hover:text-slate-600'}`}>{node}</span>
          ))}
        </div>
        <div className="h-1.5 w-full bg-slate-100 rounded-full relative">
          <motion.div 
            animate={{ left: selectedIndex !== null ? `${(selectedIndex / (nodes.length - 1 || 1)) * 100}%` : '0%' }}
            className="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-white border-4 border-blue-500 rounded-full shadow-md transition-all duration-500"
          />
        </div>
      </div>
    </motion.div>
  );
}

function VotingLayer() {
  return (
    <motion.div initial={{ y: 30, opacity: 0 }} animate={{ y: 0, opacity: 1 }} className="bg-white/95 backdrop-blur-xl border border-rose-100 p-5 rounded-3xl shadow-xl w-full pointer-events-auto">
      <div className="flex justify-between items-center mb-3">
        <h4 className="text-xs font-black text-slate-800 flex items-center gap-1.5"><AlertCircle className="w-4 h-4 text-rose-500"/> 发现 1 处边缘预算冲突</h4>
        <span className="text-[9px] bg-slate-100 text-slate-500 px-2 py-1 rounded font-bold tracking-wider">多方博弈裁决</span>
      </div>
      <div className="flex gap-3">
        <button className="flex-1 bg-rose-50/50 border border-rose-100 p-3 rounded-xl text-xs font-bold text-rose-700 flex flex-col items-center gap-2 hover:bg-rose-100/50 transition-colors">
          <span>高溢价商圈 (触发红线)</span>
        </button>
        <button className="flex-1 bg-emerald-50/50 border border-emerald-100 p-3 rounded-xl text-xs font-bold text-emerald-700 flex flex-col items-center gap-2 hover:bg-emerald-100/50 transition-colors">
          <span>当地平替 (智能体推荐)</span>
          <ThumbsUp className="w-3 h-3 text-emerald-500" />
        </button>
      </div>
    </motion.div>
  );
}