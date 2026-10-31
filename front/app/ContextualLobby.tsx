'use client';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useState } from 'react';
import {
  Users, MapPin, Sparkles, Coffee, Camera, Car, PiggyBank, ArrowRight, Check,
  Map as MapIcon, Compass, Headphones, TrendingDown, RefreshCw,
  BrainCircuit, ExternalLink,
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

// 个人主页与社区组件
import ModernProfileScreen from './components/ProfileScreen';
import CommunityPanel from './components/CommunityPanel';

import BookingHub from './components/BookingHub';
import TransportSearch from './components/TransportSearch';

// 目的地图谱数据（用于主页灵感目的地推荐）
import { PROVINCE_DATA } from './data/provinceData';

// 主控诉求与企业选择卡（自 ContextualLobby 拆分出来的展示型组件）
import { TravelModeCard, PreferenceCard } from './components/SelectCards';

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
  const [selectedMode, setSelectedMode] = useState<string>('coop');
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
    updateMember(newRole, newIntent);
    setShowEditIntentModal(false);
  };

  const renderRightPanel = () => {
    switch (selectedMode) {
      case 'solo':
        return (
          <motion.div key="solo-panel-mode" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.3 }} className="flex flex-col h-full">
            <div className="p-7 pb-5 border-b border-slate-200/60 bg-white/50">
              <h2 className="text-xl font-bold text-slate-800 mb-1 flex items-center gap-2"><Compass className="w-5 h-5 text-orange-600"/> 私人探索雷达</h2>
              <p className="text-xs text-slate-500 font-medium">系统已屏蔽外界干扰，专注为您构建沉浸式路书</p>
            </div>
            <div className="p-6 flex-1 flex flex-col gap-4">
              <div className="p-4 rounded-xl bg-white border border-slate-100 shadow-sm flex items-start gap-4">
                <div className="p-2 bg-orange-50 rounded-lg text-orange-600"><Headphones className="w-5 h-5"/></div>
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
              <h2 className="text-xl font-bold text-slate-800 mb-1 flex items-center gap-2"><TrendingDown className="w-5 h-5 text-orange-600"/> 预算与性价比</h2>
              <p className="text-xs text-slate-500 font-medium"><span className="font-bold text-slate-400">极客精算中枢</span> · 为您挖掘全网更划算的组合</p>
            </div>
            <div className="p-6 flex-1 flex flex-col gap-4">
              <div className="p-4 rounded-xl bg-gradient-to-br from-orange-50/50 to-white border border-orange-100 shadow-sm flex items-center justify-between">
                <div>
                  <p className="text-xs font-bold text-slate-400 mb-1">当前测算状态</p>
                  <p className="text-sm font-bold text-slate-800 flex items-center gap-2">
                    <TrendingDown className="w-4 h-4 text-orange-500" /> 全网比价引擎就绪，开始规划后自动拉取
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
                <span className="text-[10px] bg-stone-100 text-stone-500 font-bold px-2 py-0.5 rounded-full border border-stone-200">实时协同房间</span>
              </div>
              <p className="text-xs text-slate-500 mb-3 font-medium">基于真实房间号广播，多人跨屏实时协同</p>
              <div className="mb-3">
                <RoomConnectionStatus connection={roomConnection} retryAttempt={roomRetryAttempt} retryDelayMs={roomRetryDelayMs} memberCount={roomMembers.length} onRetry={retryRoomConnection} testId="room-status-coop" />
              </div>
              
              <div className="bg-white rounded-xl p-3 border border-slate-200 shadow-sm mb-1">
                <div className="flex justify-between items-center mb-1.5">
                  <span className="text-xs font-bold text-slate-500">当前协作房间码（分享给好友即可同行）</span>
                  <span data-testid="room-code" className="text-base font-black text-orange-600 font-mono tracking-wider flex items-center gap-1.5">
                    {roomReady ? roomCode : '正在准备…'}
                    <button onClick={copyRoomCode} className="p-1 hover:bg-orange-50 rounded-md transition-colors cursor-pointer" title="复制邀请码">
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
                  <button data-testid="join-room-submit" onClick={joinRoom} disabled={!inputRoomCode.trim()} className="px-3.5 py-1.5 bg-slate-900 hover:bg-orange-500 disabled:bg-slate-300 text-white rounded-lg text-xs font-bold transition-colors cursor-pointer disabled:cursor-not-allowed">
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
                    <img src={member.avatarUrl ? assetUrl(member.avatarUrl) : `https://api.dicebear.com/9.x/avataaars/svg?seed=${member.avatarSeed || member.name}&backgroundColor=fdeed8`} alt="avatar" className="w-9 h-9 rounded-full bg-slate-100 border border-slate-200 shrink-0 object-cover" />
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
        roomVotes={roomVotes}
        onRoomVote={sendRoomVote}
        roomConnection={roomConnection}
        roomRetryAttempt={roomRetryAttempt}
        roomRetryDelayMs={roomRetryDelayMs}
        onRetryRoom={retryRoomConnection}
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
            <header className="px-10 py-5 flex justify-between items-center border-b border-slate-200/50 bg-white/40">
              <div className="flex items-center gap-4">
                <OmniLogo className="w-10 h-10 hover:scale-105 transition-transform" />
                <div>
                  <h1 className="text-2xl font-black text-slate-800 tracking-tight">OmniRoute</h1>
                  <p className="text-xs text-slate-500 font-semibold tracking-wide mt-0.5">从想法到可执行行程</p>
                </div>
              </div>
              
              <div className="flex items-center gap-4">
                <div className="hidden sm:flex items-center gap-2 px-3.5 py-1.5 bg-white border border-slate-100 rounded-full text-xs font-bold text-slate-600 shadow-sm">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                  行程服务在线
                </div>

                <button 
                  onClick={() => setShowProfile(true)}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 bg-orange-50 border border-orange-200 text-orange-600 rounded-full text-xs font-bold hover:bg-orange-100 transition-colors cursor-pointer"
                >
                  <User className="w-3.5 h-3.5" /> 个人主页
                </button>

                <button 
                  onClick={() => setShowCommunity(true)}
                  className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-bold text-slate-600 transition-colors hover:border-orange-200 hover:bg-orange-50 hover:text-orange-600 cursor-pointer"
                >
                  <MessageCircle className="w-3.5 h-3.5" /> 社区
                </button>

                <button 
                  onClick={() => setShowTransportSearch(true)}
                  className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-bold text-slate-600 transition-colors hover:border-orange-200 hover:bg-orange-50 hover:text-orange-600 cursor-pointer"
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
                    <img src={currentUser.avatarUrl ? assetUrl(currentUser.avatarUrl) : `https://api.dicebear.com/9.x/avataaars/svg?seed=${currentUser.avatarSeed}&backgroundColor=fdeed8`} alt="avatar" className="w-full h-full object-cover" />
                  </div>
                  <div className="text-left">
                    <span className="text-xs font-black text-slate-800 block">{currentUser.nickname}</span>
                    <span className="text-[10px] text-stone-400 font-bold">SQLite 认证就绪</span>
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

            <main className="flex-1 p-8 sm:p-10 grid grid-cols-1 lg:grid-cols-12 gap-8 overflow-y-auto overflow-x-hidden">
              <div className="lg:col-span-8 flex flex-col gap-6">
                <div>
                  <h2 className="text-3xl font-extrabold text-slate-800 mb-2 tracking-tight">开启下一段旅程协同规划</h2>
                  <p className="text-slate-500 text-sm font-medium">邀请同行者加入同一个房间，一起确认节奏、预算和必去地点。</p>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <TravelModeCard id="solo" title="一人行" desc="步调全由自己掌控，定制绝对自由的私人路书。" icon={<MapPin className="w-6 h-6" />} color="text-slate-500" selected={selectedMode === 'solo'} onClick={() => setSelectedMode('solo')} />
                  <TravelModeCard id="coop" title="亲友结伴" desc="把每个人的偏好放在同一张行程里，减少来回争论。" icon={<Users className="w-6 h-6" />} color="text-slate-500" selected={selectedMode === 'coop'} onClick={() => setSelectedMode('coop')} />
                  <TravelModeCard id="pvp" title="高性价比" desc="精打细算，利用算法匹配最优的体验成本比。" icon={<PiggyBank className="w-6 h-6" />} color="text-slate-500" selected={selectedMode === 'pvp'} onClick={() => setSelectedMode('pvp')} />
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
                      {selectedMode === 'solo' && "专注自我，深度探索"}
                      {selectedMode === 'coop' && '什么是“基于房间的多人协同规划”？'}
                      {selectedMode === 'pvp' && "极致性价比是如何做到的？"}
                    </h3>
                    <p className="text-xs text-slate-600 leading-relaxed font-medium">
                      {selectedMode === 'solo' && "系统会减少社交与分享类内容，按单人偏好模型为你排序路线。"}
                      {selectedMode === 'coop' && `同一房间码 [${roomCode}] 内的所有真实在线成员，会各自提出偏好与首选方案；系统会优先保证最不满意的那位成员也不至于吃亏，并把每位成员的让步与最终满意度逐条记录。专家建议同时给出交通、住宿、餐饮等提案。`}
                      {selectedMode === 'pvp' && "比价引擎会根据距离、评价、交通成本构建价格网络，逐项裁剪掉不必要的开销。"}
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
              <button type="button" aria-label="打开灵感目的地图谱" onClick={() => setShowDestinationMap(true)} className="group w-full text-left bg-white/60 backdrop-blur-md border border-slate-200/70 rounded-2xl p-5 cursor-pointer hover:border-orange-300 hover:shadow-lg hover:shadow-orange-100/50 transition-all">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Compass className="w-4 h-4 text-orange-500" />
                    <h3 className="text-sm font-extrabold text-slate-800">灵感目的地图谱</h3>
                  </div>
                  <span className="text-[10px] font-black text-orange-700 bg-orange-50 border border-orange-100 px-2 py-0.5 rounded-full">{Object.keys(PROVINCE_DATA).length} 目的地</span>
                </div>
                <p className="text-[11px] text-slate-500 font-medium leading-relaxed mb-3">交互式全国目的地地图：搜索筛选、点击标记看景点详情、一键开启智能规划。</p>
                <div className="flex flex-wrap gap-1.5">
                  {TOP_DESTINATIONS.map(c => (
                    <span key={c} className="text-[11px] bg-orange-50 text-orange-700 px-2.5 py-1 rounded-full font-bold group-hover:bg-orange-100 transition-colors">{shortenRegionName(c)}</span>
                  ))}
                </div>
                <span className="inline-flex items-center gap-1 text-[11px] text-orange-600 font-bold mt-3">打开交互式地图 <ArrowRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" /></span>
              </button>

              <button type="button" aria-label="进入同行者社区" onClick={() => setShowCommunity(true)} className="w-full text-left bg-white/60 backdrop-blur-md border border-slate-200/70 rounded-2xl p-5 cursor-pointer hover:border-orange-300 hover:shadow-md transition-all">
                <div className="flex items-center gap-2 mb-3">
                  <MessageCircle className="w-4 h-4 text-orange-500" />
                  <h3 className="text-sm font-extrabold text-slate-800">同行者社区</h3>
                </div>
                <p className="text-xs text-slate-500 leading-relaxed font-medium">分享满意行程，点赞、评论、回复，让高质量路书被更多旅行者借鉴。</p>
                <span className="inline-flex items-center gap-1 text-[11px] text-orange-600 font-bold mt-3">进入社区 <ArrowRight className="w-3 h-3" /></span>
              </button>

              <div className="bg-white/60 backdrop-blur-md border border-slate-200/70 rounded-2xl p-5">
                <div className="flex items-center gap-2 mb-3">
                  <BrainCircuit className="w-4 h-4 text-orange-600" />
                  <h3 className="text-sm font-extrabold text-slate-800">偏好学习记录</h3>
                </div>
                {[
                  '融合全网评价与真实反馈，路线持续自我修正',
                  '社区高质量行程作为软引导，辅助方案生成',
                  '成员让位受各自预算约束，协商逐轮留痕'
                ].map((t, i) => (
                  <div key={i} className="flex items-start gap-2 mb-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-orange-400 mt-1.5 shrink-0"></span>
                    <p className="text-[11px] text-slate-500 font-medium leading-relaxed">{t}</p>
                  </div>
                ))}
              </div>
            </div>

            <footer className="p-6 sm:p-8 border-t border-slate-200/50 bg-white/30 flex justify-between items-center">
              <span className="text-xs text-slate-400 font-bold hidden sm:inline">
                当前协同房间：<strong className="text-slate-700 font-mono">{roomCode}</strong>（将此房间码分享给同行好友即可同屏规划）
              </span>
              <Link href="/capabilities" className="mr-3 text-[11px] font-bold text-slate-400 underline-offset-2 hover:text-orange-600 hover:underline">能力与数据来源</Link>
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

