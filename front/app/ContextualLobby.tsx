'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useState } from 'react';
import {
  Users, MapPin, Sparkles, Coffee, Camera, Car, ArrowRight, Check,
  Map as MapIcon, Compass,
  RefreshCw, ChevronDown,
  ExternalLink,
  User, LogOut, Edit3, Copy,
  MessageCircle,
  Train
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { assetUrl } from './lib/utils';
import { shortenRegionName } from './lib/lobbyUtils';
import { useUserSessionStore } from './lib/stores/userSession';
import { useRoomCollaborationStore } from './lib/stores/roomCollaboration';

// 工作台（规划/执行/复盘）已按业务域拆到独立文件，页面只负责大厅入口。
import UnifiedWorkspace from './components/UnifiedWorkspace';

// 修改诉求画像弹窗（自 ContextualLobby 拆分出来的展示型组件）
import EditIntentModal from './components/EditIntentModal';

// 系统图标与登录门户（自 ContextualLobby 拆分出来的展示型组件）
import OmniLogo from './components/OmniLogo';
import AuthPortalScreen from './components/AuthPortalScreen';
import RoomConnectionStatus from './components/RoomConnectionStatus';
// 顶部「服务状态」：真实健康检查，不再用一句「服务在线」概括所有能力
import ServiceHealthIndicator from './components/ServiceHealthIndicator';

// 个人主页与社区组件
import ModernProfileScreen from './components/ProfileScreen';
import CommunityPanel from './components/CommunityPanel';

import BookingHub from './components/BookingHub';
import TransportSearch from './components/TransportSearch';

// 目的地图谱数据（用于主页灵感目的地推荐）
import { PROVINCE_DATA } from './data/provinceData';

// 偏好选择卡（自 ContextualLobby 拆分出来的展示型组件）
import { PreferenceCard } from './components/SelectCards';

// 全站评分最高的目的地（用于主页灵感目的地图谱卡片）
const TOP_DESTINATIONS = Object.entries(PROVINCE_DATA)
  .sort((a, b) => parseFloat(b[1].summary.score) - parseFloat(a[1].summary.score))
  .slice(0, 6)
  .map(([name]) => name);

// 灵感目的地图谱（高德瓦片 + MapLibre 交互式目的地可视化）
const DestinationMap = dynamic(() => import('./DestinationMap'), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 z-[130] bg-slate-950 flex items-center justify-center">
      <RefreshCw className="w-6 h-6 text-orange-500 animate-spin" />
    </div>
  )
});


// =========================================================================
// 主入口：认证判断 -> 大厅界面 -> 多智能体决策工作区
// =========================================================================
export default function ContextualLobby() {
  // 首页只回答三件事：去哪、几天、几个人，以及必须满足什么。其余入口收进「更多」。
  const [destination, setDestination] = useState('');
  const [days, setDays] = useState(3);
  const [travelers, setTravelers] = useState(2);
  const [preferBudget, setPreferBudget] = useState(false);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [showPreferenceModal, setShowPreferenceModal] = useState(false);
  const [showEditIntentModal, setShowEditIntentModal] = useState(false);

  const [selectedRole, setSelectedRole] = useState<string>('寻味探索');
  const [myIntent, setMyIntent] = useState<string>('必须安排当地特色正宗老字号，不吃预制菜');

  const [isConnecting, setIsConnecting] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [showCommunity, setShowCommunity] = useState(false);
  const [showTransportSearch, setShowTransportSearch] = useState(false);
  const [showBookingHub, setShowBookingHub] = useState(false);
  const [showDestinationMap, setShowDestinationMap] = useState(false);
  const [planDestination, setPlanDestination] = useState<string | null>(null);

  // 用户会话与房间协同收敛到独立 Store（见 app/lib/stores）。
  const { currentUser, currentUserRef, authReady, signIn, updateProfile, signOut } = useUserSessionStore();
  const {
    roomCode, roomReady, roomError, setRoomError,
    roomMembers, roomVotes,
    roomSocketReady, roomSocketReadyRef,
    roomConnection, roomRetryAttempt, roomRetryDelayMs, retryRoomConnection,
    inputRoomCode, setInputRoomCode, copied,
    joinRoom, copyRoomCode, sendRoomVote, updateMember,
  } = useRoomCollaborationStore({ currentUser, currentUserRef, role: selectedRole, intent: myIntent });

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
          signIn(user);
        }} 
      />
    );
  }

  // 出行方式由「几个人 + 是否优先控制预算」推导，不再让用户先选三种模式。
  const selectedMode = preferBudget ? 'pvp' : travelers > 1 ? 'coop' : 'solo';

  // 大厅收集的「去哪、几天、几人、必须满足什么」直接变成规划诉求，
  // 进入工作区后仍可修改，不会锁死。
  const lobbyIntent = [
    destination.trim() ? `去${destination.trim()}玩${days}天` : '',
    travelers > 1 ? `${travelers}人同行` : '1人出行',
    preferBudget ? '优先控制预算' : '',
    myIntent.trim() ? `必须满足：${myIntent.trim()}` : '',
  ].filter(Boolean).join('，');

  const handleBeginPlan = () => {
    // The preference dialog is a local interaction and must remain usable while
    // the collaboration room is still bootstrapping. The workspace will show
    // a recoverable connection state if the room is not ready yet.
    if (!roomReady) setRoomError('协作房间仍在同步，先确认偏好即可进入规划');
    setShowPreferenceModal(true);
  };

  const handleStartPlanFromDestination = (city: string) => {
    setDestination(city);
    setPlanDestination(city);
    setShowDestinationMap(false);
    setShowPreferenceModal(true); // 复用「确认偏好」弹窗
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
    setRoomError('正在连接协作房间…');

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
        setRoomError('房间连接超时，请检查网络后重试');
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
    updateMember(newRole, newIntent);
    setShowEditIntentModal(false);
  };


  if (showWorkspace) {
    return (
      <UnifiedWorkspace 
        mode={selectedMode} 
        role={selectedRole} 
        roomCode={roomCode} 
        roomMembers={roomMembers}
        currentUser={currentUser}
        roomVotes={roomVotes}
        onRoomVote={sendRoomVote}
        roomConnection={roomConnection}
        roomRetryAttempt={roomRetryAttempt}
        roomRetryDelayMs={roomRetryDelayMs}
        onRetryRoom={retryRoomConnection}
        initialDestination={planDestination}
        initialIntent={lobbyIntent}
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
        onProfileChange={(patch) => updateProfile(patch)}
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
            <header className="px-6 py-4 sm:px-10 sm:py-5 flex justify-between items-center border-b border-slate-200/50 bg-white/40">
              <div className="flex items-center gap-3 sm:gap-4">
                <OmniLogo className="w-9 h-9 sm:w-10 sm:h-10 hover:scale-105 transition-transform" />
                <div>
                  <h1 className="text-xl sm:text-2xl font-black text-slate-800 tracking-tight">OmniRoute</h1>
                  <p className="text-xs text-slate-500 font-semibold tracking-wide mt-0.5 hidden sm:block">从想法到可执行行程</p>
                </div>
              </div>

              <div className="flex items-center gap-2 sm:gap-3">
                <ServiceHealthIndicator />

                <div className="relative">
                  <button
                    type="button"
                    data-testid="lobby-more"
                    aria-expanded={showMoreMenu}
                    onClick={() => setShowMoreMenu((value) => !value)}
                    className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-bold text-slate-600 transition-colors hover:border-orange-200 hover:text-orange-600"
                  >
                    更多 <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showMoreMenu ? 'rotate-180' : ''}`} />
                  </button>
                  {showMoreMenu && (
                    <div
                      data-testid="lobby-more-menu"
                      className="absolute right-0 top-full z-40 mt-2 flex w-56 flex-col gap-1 rounded-2xl border border-slate-200 bg-white p-2 shadow-xl"
                    >
                      <button onClick={() => { setShowMoreMenu(false); setShowProfile(true); }} className="flex items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-bold text-slate-600 hover:bg-orange-50 hover:text-orange-600">
                        <User className="h-3.5 w-3.5" /> 个人主页
                      </button>
                      <button onClick={() => { setShowMoreMenu(false); setShowCommunity(true); }} className="flex items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-bold text-slate-600 hover:bg-orange-50 hover:text-orange-600">
                        <MessageCircle className="h-3.5 w-3.5" /> 社区
                      </button>
                      <button onClick={() => { setShowMoreMenu(false); setShowTransportSearch(true); }} className="flex items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-bold text-slate-600 hover:bg-orange-50 hover:text-orange-600">
                        <Train className="h-3.5 w-3.5" /> 订票查询
                      </button>
                      <button onClick={() => { setShowMoreMenu(false); setShowBookingHub(true); }} className="flex items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-bold text-slate-600 hover:bg-orange-50 hover:text-orange-600">
                        <ExternalLink className="h-3.5 w-3.5" /> 官方渠道
                      </button>
                      <button onClick={() => { setShowMoreMenu(false); setShowDestinationMap(true); }} className="flex items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-bold text-slate-600 hover:bg-orange-50 hover:text-orange-600">
                        <Compass className="h-3.5 w-3.5" /> 灵感目的地图谱
                      </button>
                      <div className="mt-1 border-t border-slate-100 px-3 py-2 text-[11px] leading-relaxed text-slate-400">
                        3D 态势雷达、碳排放、AR 导览、高级信息与模型治理在规划工作区里按需展开。
                      </div>
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-2 bg-white border border-slate-200/80 px-2 py-1.5 sm:gap-3 sm:px-3 rounded-2xl shadow-xs">
                  <div className="w-8 h-8 rounded-full overflow-hidden bg-orange-100 border border-orange-200">
                    <img src={currentUser.avatarUrl ? assetUrl(currentUser.avatarUrl) : `https://api.dicebear.com/9.x/avataaars/svg?seed=${currentUser.avatarSeed}&backgroundColor=fdeed8`} alt="avatar" className="w-full h-full object-cover" />
                  </div>
                  <div className="text-left hidden sm:block">
                    <span className="text-xs font-black text-slate-800 block">{currentUser.nickname}</span>
                    <span className="text-[10px] text-stone-400 font-bold">已登录</span>
                  </div>
                  <button
                    onClick={signOut}
                    className="ml-1 p-1.5 hover:bg-rose-50 text-slate-400 hover:text-rose-500 rounded-lg transition-colors cursor-pointer"
                    title="退出登录"
                  >
                    <LogOut className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </header>

            <main className="flex-1 px-5 py-6 sm:px-10 sm:py-8 overflow-y-auto overflow-x-hidden">
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
                <section className="flex flex-col gap-4 lg:col-span-8">
                  <div>
                    <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-800 tracking-tight">开启下一段旅程协同规划</h2>
                    <p className="mt-1 text-sm font-medium text-slate-500">
                      三步就能开始：先确定去哪、几天、几个人，再写清必须满足的条件，最后生成方案。
                    </p>
                  </div>

                  <div className="rounded-3xl border border-slate-200 bg-white/80 p-5 shadow-sm sm:p-6">
                    <div className="mb-4 flex items-center gap-3">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-orange-500 text-xs font-black text-white">1</span>
                      <div>
                        <h3 className="text-sm font-extrabold text-slate-800">去哪、几天、几个人</h3>
                        <p className="text-xs font-medium text-slate-500">先给规划一个确定的起点</p>
                      </div>
                    </div>

                    <label className="block">
                      <span className="mb-1.5 flex items-center gap-1.5 text-xs font-bold text-slate-600"><MapPin className="h-3.5 w-3.5 text-orange-500" /> 目的地</span>
                      <input
                        data-testid="lobby-destination"
                        value={destination}
                        onChange={(event) => setDestination(event.target.value)}
                        placeholder="例如：成都、西安、洛阳"
                        className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-semibold text-slate-800 outline-none focus:border-orange-500"
                      />
                    </label>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {['北京', '上海', '成都', '西安', '杭州', '洛阳'].map((city) => (
                        <button
                          key={city}
                          type="button"
                          onClick={() => setDestination(city)}
                          className={`rounded-full border px-2.5 py-1 text-[11px] font-bold transition-colors ${destination === city ? 'border-orange-300 bg-orange-50 text-orange-700' : 'border-slate-200 bg-white text-slate-500 hover:border-orange-200 hover:text-orange-600'}`}
                        >
                          {city}
                        </button>
                      ))}
                    </div>

                    <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3">
                        <span className="text-xs font-bold text-slate-600">几天</span>
                        <div className="mt-2 flex items-center justify-between">
                          <button type="button" aria-label="减少天数" onClick={() => setDays((value) => Math.max(1, value - 1))} className="h-8 w-8 rounded-lg border border-slate-200 bg-white text-sm font-black text-slate-600 hover:border-orange-300 hover:text-orange-600">−</button>
                          <span data-testid="lobby-days" className="text-lg font-black text-slate-800">{days} 天</span>
                          <button type="button" aria-label="增加天数" onClick={() => setDays((value) => Math.min(15, value + 1))} className="h-8 w-8 rounded-lg border border-slate-200 bg-white text-sm font-black text-slate-600 hover:border-orange-300 hover:text-orange-600">+</button>
                        </div>
                      </div>
                      <div className="rounded-2xl border border-slate-200 bg-slate-50/70 px-4 py-3">
                        <span className="flex items-center gap-1.5 text-xs font-bold text-slate-600"><Users className="h-3.5 w-3.5 text-orange-500" /> 同行人数</span>
                        <div className="mt-2 flex items-center justify-between">
                          <button type="button" aria-label="减少人数" onClick={() => setTravelers((value) => Math.max(1, value - 1))} className="h-8 w-8 rounded-lg border border-slate-200 bg-white text-sm font-black text-slate-600 hover:border-orange-300 hover:text-orange-600">−</button>
                          <span data-testid="lobby-travelers" className="text-lg font-black text-slate-800">{travelers} 人</span>
                          <button type="button" aria-label="增加人数" onClick={() => setTravelers((value) => Math.min(10, value + 1))} className="h-8 w-8 rounded-lg border border-slate-200 bg-white text-sm font-black text-slate-600 hover:border-orange-300 hover:text-orange-600">+</button>
                        </div>
                      </div>
                    </div>

                    <label className="mt-3 flex items-center gap-2 text-xs font-bold text-slate-600">
                      <input type="checkbox" data-testid="lobby-budget-first" checked={preferBudget} onChange={(event) => setPreferBudget(event.target.checked)} className="h-4 w-4 accent-orange-500" />
                      尽量控制预算
                    </label>

                    <div className="my-5 h-px bg-slate-100" />

                    <div className="mb-3 flex items-center gap-3">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-orange-500 text-xs font-black text-white">2</span>
                      <div>
                        <h3 className="text-sm font-extrabold text-slate-800">必须满足什么</h3>
                        <p className="text-xs font-medium text-slate-500">不写清楚，方案只能靠猜</p>
                      </div>
                    </div>
                    <div className="flex items-start justify-between gap-3 rounded-2xl bg-slate-50 p-4">
                      <div className="min-w-0">
                        <span className="inline-flex rounded-md bg-orange-100 px-2 py-0.5 text-xs font-bold text-orange-700">{selectedRole}</span>
                        <p className="mt-1.5 text-xs font-medium leading-relaxed text-slate-600">{myIntent}</p>
                      </div>
                      <button
                        type="button"
                        data-testid="lobby-edit-intent"
                        onClick={() => setShowEditIntentModal(true)}
                        className="flex shrink-0 items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 transition-colors hover:border-orange-200 hover:text-orange-600"
                      >
                        <Edit3 className="h-3.5 w-3.5" /> 修改
                      </button>
                    </div>

                    <div className="my-5 h-px bg-slate-100" />

                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex items-center gap-3">
                        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-orange-500 text-xs font-black text-white">3</span>
                        <div>
                          <h3 className="text-sm font-extrabold text-slate-800">生成方案</h3>
                          <p className="text-xs font-medium text-slate-500">生成后仍可修改、对比和导出</p>
                        </div>
                      </div>
                      <button
                        data-testid="begin-plan"
                        onClick={handleBeginPlan}
                        className="flex items-center justify-center gap-2 rounded-2xl bg-orange-500 px-6 py-3 text-sm font-bold text-white shadow-lg shadow-orange-200/50 transition-all hover:-translate-y-0.5 hover:bg-orange-600"
                      >
                        生成方案 <ArrowRight className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </section>

                <aside className="flex flex-col gap-4 lg:col-span-4">
                  <div className="rounded-3xl border border-slate-200 bg-white/80 p-5 shadow-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="flex items-center gap-2 text-base font-extrabold text-slate-800"><Users className="h-4 w-4 text-orange-500" /> 加入房间</h3>
                      <span data-testid="room-code" className="flex items-center gap-1.5 font-mono text-sm font-black tracking-wider text-orange-600">
                        {roomReady ? roomCode : '准备中…'}
                        <button onClick={copyRoomCode} className="rounded-md p-1 transition-colors hover:bg-orange-50" title="复制房间码">
                          {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5 text-orange-500" />}
                        </button>
                      </span>
                    </div>
                    <p className="mt-1 text-xs font-medium text-slate-500">把房间码发给同行好友，一起确认节奏、预算和必去地点。</p>
                    <div className="mt-3">
                      <RoomConnectionStatus connection={roomConnection} retryAttempt={roomRetryAttempt} retryDelayMs={roomRetryDelayMs} memberCount={roomMembers.length} onRetry={retryRoomConnection} testId="room-status-lobby-home" />
                    </div>
                    {roomError && <p className="mt-2 text-[11px] font-bold text-rose-500">{roomError}</p>}
                    <div className="mt-3 flex gap-1.5">
                      <input
                        data-testid="join-room-code"
                        type="text"
                        placeholder="输入好友分享的房间码"
                        value={inputRoomCode}
                        onChange={(event) => setInputRoomCode(event.target.value)}
                        className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs font-bold uppercase text-slate-800 outline-none focus:border-orange-500"
                      />
                      <button data-testid="join-room-submit" onClick={joinRoom} disabled={!inputRoomCode.trim()} className="shrink-0 rounded-lg bg-slate-900 px-3.5 py-2 text-xs font-bold text-white transition-colors hover:bg-orange-500 disabled:cursor-not-allowed disabled:bg-slate-300">
                        加入
                      </button>
                    </div>
                    {roomMembers.length > 0 && (
                      <ul className="mt-3 space-y-1.5">
                        {roomMembers.map((member) => (
                          <li key={member.id} className="flex items-center justify-between rounded-xl border border-slate-100 bg-white px-2.5 py-2">
                            <div className="min-w-0">
                              <p className="truncate text-xs font-bold text-slate-800">{member.name}{member.id === currentUser.id ? '（我）' : ''}</p>
                              <p className="truncate text-[11px] font-medium text-slate-500">{member.role} · {member.intent}</p>
                            </div>
                            {member.id === currentUser.id && (
                              <button onClick={() => setShowEditIntentModal(true)} className="shrink-0 rounded-lg p-1 text-orange-600 hover:bg-orange-100" title="调整我的条件">
                                <Edit3 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <button
                    type="button"
                    aria-label="打开灵感目的地图谱"
                    onClick={() => setShowDestinationMap(true)}
                    className="group rounded-3xl border border-slate-200 bg-white/80 p-5 text-left shadow-sm transition-all hover:border-orange-300 hover:shadow-lg hover:shadow-orange-100/50"
                  >
                    <div className="mb-2 flex items-center justify-between">
                      <h3 className="flex items-center gap-2 text-base font-extrabold text-slate-800"><Compass className="h-4 w-4 text-orange-500" /> 查看示例</h3>
                      <span className="rounded-full border border-orange-100 bg-orange-50 px-2 py-0.5 text-[10px] font-black text-orange-700">{Object.keys(PROVINCE_DATA).length} 个目的地</span>
                    </div>
                    <p className="text-xs font-medium leading-relaxed text-slate-500">还没想好去哪？打开灵感目的地图谱，看看热门目的地的玩法，再一键开始规划。</p>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {TOP_DESTINATIONS.map((city) => (
                        <span key={city} className="rounded-full bg-orange-50 px-2.5 py-1 text-[11px] font-bold text-orange-700 transition-colors group-hover:bg-orange-100">{shortenRegionName(city)}</span>
                      ))}
                    </div>
                    <span className="mt-3 inline-flex items-center gap-1 text-[11px] font-bold text-orange-600">打开交互式地图 <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" /></span>
                  </button>
                </aside>
              </div>
            </main>

            <footer className="flex flex-wrap items-center gap-3 border-t border-slate-200/50 bg-white/30 px-5 py-4 sm:px-10">
              <span className="hidden text-xs font-bold text-slate-500 sm:inline">
                当前房间：<strong className="font-mono text-slate-700">{roomCode}</strong>
              </span>
              <Link href="/capabilities" className="text-[11px] font-bold text-slate-400 underline-offset-2 hover:text-orange-600 hover:underline">能力与数据来源</Link>
              <p className="ml-auto text-[11px] font-medium text-slate-400">生成方案后仍可逐项修改，不会锁死行程</p>
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
                <div className="mb-2 flex items-center gap-2 text-xs font-black text-orange-600"><Sparkles className="h-4 w-4" /> 第 2 步 · 必须满足什么</div>
                <h3 className="text-2xl font-extrabold text-slate-900">这趟旅程，你最期待什么？</h3>
                <p className="mt-1 text-xs sm:text-sm text-slate-500 font-medium">选一个主偏好，进入规划后仍可随时调整，不会锁死行程。</p>
              </div>
              <div className="grid grid-cols-1 gap-3 px-6 py-5 sm:grid-cols-2 sm:px-8">
                <PreferenceCard id="foodie" icon={<Coffee/>} title="寻味探索" desc="美食驱动，为您匹配地道餐馆。" selected={selectedRole === '寻味探索'} onClick={() => setSelectedRole('寻味探索')} color="border-orange-500 bg-orange-50/80 text-orange-600" />
                <PreferenceCard id="photo" icon={<Camera/>} title="视觉体验" desc="出片导向，精准匹配最佳摄影光线。" selected={selectedRole === '视觉体验'} onClick={() => setSelectedRole('视觉体验')} color="border-orange-500 bg-orange-50/80 text-orange-600" />
                <PreferenceCard id="chill" icon={<Car/>} title="休闲漫步" desc="拒绝特种兵，安排宽裕休息漫游。" selected={selectedRole === '休闲漫步'} onClick={() => setSelectedRole('休闲漫步')} color="border-orange-500 bg-orange-50/80 text-orange-600" />
                <PreferenceCard id="hardcore" icon={<MapIcon/>} title="深度探索" desc="行程紧凑，打卡最多核心文旅地标。" selected={selectedRole === '深度探索'} onClick={() => setSelectedRole('深度探索')} color="border-orange-500 bg-orange-50/80 text-orange-600" />
              </div>
              <div className="mx-6 mb-5 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs sm:mx-8">
                <RoomConnectionStatus
                  connection={roomConnection}
                  retryAttempt={roomRetryAttempt}
                  retryDelayMs={roomRetryDelayMs}
                  memberCount={roomMembers.length}
                  onRetry={retryRoomConnection}
                  testId="room-status-lobby"
                />
              </div>
              <div className="flex gap-3 border-t border-slate-100 bg-white px-6 py-4 sm:px-8">
                <button onClick={() => !isConnecting && setShowPreferenceModal(false)} className="flex-1 py-3 bg-slate-50 border border-slate-200 text-slate-600 font-bold rounded-xl hover:bg-slate-100 transition-colors cursor-pointer text-sm">返回调整</button>
                <button onClick={handleConfirmSync} disabled={!selectedRole || isConnecting} className={`flex-[1.5] py-3 rounded-xl font-bold flex items-center justify-center gap-2 transition-all cursor-pointer text-sm ${!selectedRole ? 'bg-slate-100 text-slate-400' : isConnecting ? 'bg-orange-500 text-white' : 'bg-orange-500 text-white hover:bg-orange-600 shadow-md shadow-orange-200/50'}`}>
                  {isConnecting ? <><RefreshCw className="w-4 h-4 animate-spin"/>激活规划助手...</> : <>查看规划方案 <ArrowRight className="w-4 h-4" /></>}
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


// -------------------------------------------------------------------------
// 大厅与工具子组件
// -------------------------------------------------------------------------

// ============== 同行网络协同沙盘 · 实时动态版 ==============

