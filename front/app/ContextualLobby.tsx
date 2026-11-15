'use client';

import { JourneyLobbyHero } from './components/JourneyEditorial';

import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import {
  Users, MapPin, Sparkles, Coffee, Camera, Car, ArrowRight, Check,
  Map as MapIcon, Compass,
  RefreshCw, ChevronDown,
  ExternalLink,
  User, LogOut, Edit3, Copy,
  MessageCircle,
  ShieldCheck,
  Train
} from 'lucide-react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { generatedAvatarUrl } from './lib/avatarArt';
import { assetUrl } from './lib/utils';
import { shortenRegionName } from './lib/lobbyUtils';
import { useUserSessionStore } from './lib/stores/userSession';
import { useRoomCollaborationStore } from './lib/stores/roomCollaboration';
import { readLobbySnapshot, writeLobbySnapshot, clearWorkspaceSnapshots } from './lib/workspaceSnapshot';

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

// 成员专属等候页：加入别人房间后等房主开始规划的页面
import RoomWaitingScreen from './components/RoomWaitingScreen';

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

// 目的地情绪光球：选目的地时，大厅背景环境光平滑过渡到对应色相。
// 只动背景氛围层——品牌主 CTA、语义色（good/warn/risk）不参与，颜色仍是信息。
const DEFAULT_MOOD = { orbA: 'rgba(251, 146, 60, 0.10)', orbB: 'rgba(251, 191, 36, 0.10)' };
const LOBBY_MOODS: Record<string, { orbA: string; orbB: string }> = {
  '成都': { orbA: 'rgba(16, 185, 129, 0.12)', orbB: 'rgba(239, 68, 68, 0.09)' },   // 竹青 + 火锅暖红
  '西安': { orbA: 'rgba(217, 119, 6, 0.12)', orbB: 'rgba(190, 18, 60, 0.09)' },    // 唐金 + 绯红
  '杭州': { orbA: 'rgba(20, 184, 166, 0.12)', orbB: 'rgba(251, 146, 60, 0.10)' },  // 湖绿 + 暖橙
  '洛阳': { orbA: 'rgba(190, 24, 93, 0.10)', orbB: 'rgba(217, 119, 6, 0.11)' },    // 牡丹红 + 鎏金
  '北京': { orbA: 'rgba(185, 28, 28, 0.10)', orbB: 'rgba(202, 138, 4, 0.11)' },    // 朱红 + 琉璃金
  '上海': { orbA: 'rgba(14, 116, 144, 0.11)', orbB: 'rgba(251, 146, 60, 0.10)' },  // 浦江蓝 + 暖橙
};

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

  // 浏览器「返回」键：工作区 / 个人主页 / 社区都是同一个地址下的客户端视图，原本没有
  // 历史记录，按返回键会直接退回上一个地址（表现就是「整站回到登录页」）。这里在打开
  // 子页面时压入一条记录，返回键只负责关掉当前子页面；子页面之间切换不重复压栈。
  const subScreenOpen = showWorkspace || showProfile || showCommunity;
  const subScreenEntryRef = useRef(false);
  useEffect(() => {
    const onPopState = () => {
      setShowWorkspace(false);
      setShowProfile(false);
      setShowCommunity(false);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);
  useEffect(() => {
    if (subScreenOpen && !subScreenEntryRef.current) {
      subScreenEntryRef.current = true;
      window.history.pushState({ omniSubScreen: true }, '');
    } else if (!subScreenOpen) {
      subScreenEntryRef.current = false;
    }
  }, [subScreenOpen]);

  // 用户会话与房间协同收敛到独立 Store（见 app/lib/stores）。
  const { currentUser, currentUserRef, authReady, signIn, updateProfile, signOut } = useUserSessionStore();
  const {
    roomCode, roomReady, roomError, setRoomError,
    roomMembers, roomVotes, roomComments, sendNodeComment, ackNodeComment,
    roomSocketReady, roomSocketReadyRef,
    roomConnection, roomAuthExpired, roomRetryAttempt, roomRetryDelayMs, retryRoomConnection,
    inputRoomCode, setInputRoomCode, copied,
    joinRoom, copyRoomCode, regenerateRoom, sendRoomVote, updateMember,
    isHost, roomView, sendRoomView, followHost, setFollowHost, broadcastRoomPlan,
  } = useRoomCollaborationStore({ currentUser, currentUserRef, role: selectedRole, intent: myIntent });

  // Phase 2（暖橙空间沙盘）：
  // - reducedMotion：步进数字翻页在「减少动态」下直接落位，不做旋转；
  // - 房间波纹：roomMembers 的真实变化（队友加入/离开/修改必须满足的条件）
  //   触发一次 .room-ripple 扩散，靠 key 重放动画，不做常驻雷达。
  const reducedMotion = useReducedMotion() ?? false;
  const [roomRippleKey, setRoomRippleKey] = useState(0);
  const membersSignatureRef = useRef('');
  const membersSignature = roomMembers
    .map((member) => `${member.id}:${member.role}:${member.intent}`)
    .join('|');
  useEffect(() => {
    const baselineEmpty = membersSignatureRef.current === '';
    if (roomConnection !== 'connected' || baselineEmpty) {
      // 未连接 / 基线未建立：只同步基线，不触发波纹（避免首帧、断线期误报）。
      membersSignatureRef.current = membersSignature;
      return;
    }
    if (membersSignatureRef.current !== membersSignature) {
      membersSignatureRef.current = membersSignature;
      setRoomRippleKey((key) => key + 1);
    }
  }, [membersSignature, roomConnection]);

  // 跟随房主：成员默认跟随，房主开始规划时自动进入同一个页面。
  // 房主本人、关闭跟随、或已在工作台时不动作。
  useEffect(() => {
    if (isHost || !followHost || showWorkspace) return;
    if (!roomView || roomView.view !== 'planning') return;
    if (roomView.destination) {
      setDestination(roomView.destination);
      setPlanDestination(roomView.destination);
    }
    setShowWorkspace(true);
  }, [isHost, followHost, showWorkspace, roomView]);

  // 房主回到大厅时，跟随中的成员一起退出规划页（随时可在工作台关掉跟随）。
  useEffect(() => {
    if (isHost || !followHost || !showWorkspace) return;
    if (roomView?.view === 'lobby') setShowWorkspace(false);
  }, [isHost, followHost, showWorkspace, roomView?.view]);

  // 刷新恢复：登录态由 refresh cookie 续期，这里把"停在哪个页面、填了什么目的地"
  // 一起从 sessionStorage 里拿回来，避免每次刷新都掉回大厅首页。
  const lobbyHydratedRef = useRef(false);
  useEffect(() => {
    if (!authReady) return;
    const snapshot = readLobbySnapshot(currentUser?.id);
    if (snapshot) {
      if (typeof snapshot.destination === 'string') setDestination(snapshot.destination);
      if (Number.isFinite(snapshot.days)) setDays(snapshot.days);
      if (Number.isFinite(snapshot.travelers)) setTravelers(snapshot.travelers);
      setPlanDestination(snapshot.planDestination ?? null);
      if (snapshot.showWorkspace) setShowWorkspace(true);
    }
    lobbyHydratedRef.current = true;
  }, [authReady, currentUser?.id]);

  // 快照只在恢复完成之后才写：否则挂载首帧的默认值会把上次的页面覆盖掉。
  useEffect(() => {
    if (!authReady || !lobbyHydratedRef.current || !currentUser?.id) return;
    writeLobbySnapshot(currentUser.id, { destination, days, travelers, planDestination, showWorkspace });
  }, [authReady, currentUser?.id, destination, days, travelers, planDestination, showWorkspace]);

  // 未登录（含主动退出）时清空快照：换个账号登录不该自动跳进上一个人的工作台。
  useEffect(() => {
    if (!authReady || currentUser?.isLoggedIn) return;
    clearWorkspaceSnapshots();
  }, [authReady, currentUser?.isLoggedIn]);

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
          // 管理员登录后直接进入运维看板：这个账号的用途就是看板和审核，
          // 停在普通用户大厅等于没有入口。看板里有「返回大厅」，不锁死。
          if (user?.isAdmin) window.location.assign('/admin');
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
    // 房主在线时，成员不单独发起规划：方案由房主统一生成，成员跟着同一份方案，
    // 否则两边各跑一次推演、还会抢占房间唯一的推演名额，房主反而点不动。
    if (memberWaitingForHost) return;
    // The preference dialog is a local interaction and must remain usable while
    // the collaboration room is still bootstrapping. The workspace will show
    // a recoverable connection state if the room is not ready yet.
    if (!roomReady) setRoomError('协作房间仍在同步，先确认偏好即可进入规划');
    setShowPreferenceModal(true);
  };

  const handleStartPlanFromDestination = (city: string) => {
    if (memberWaitingForHost) return;
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
    // 房主进入规划页时广播一次视图，跟随的成员自动进入同一页面。
    if (isHost) {
      sendRoomView('planning', {
        destination: planDestination || destination || undefined,
        days,
        travelers,
      });
    }
  };

  // 跟随房主：成员离开工作台时不必广播；房主离开则通知成员一起回到大厅。
  const handleLeaveWorkspace = () => {
    setShowWorkspace(false);
    if (isHost) sendRoomView('lobby');
  };

  // 大厅里提示「谁在规划」：优先用成员列表里的昵称，其次用广播里带的昵称。
  const roomFollowName =
    roomMembers.find((member) => String(member.id) === String(roomView?.user_id))?.name ||
    roomView?.user_name ||
    '房主';

  // 成员专属等候页的显示条件：
  // - 自己是通过房间码加入的成员（isHost=false）；
  // - 房间里除了自己还有别人（房主在线），否则留在普通大厅；
  // - 没有被自动跟随带进规划页。
  const hostOnline = roomMembers.some((member) => String(member.id) !== String(currentUser.id));
  // 成员在房主的房间里只能等房主统一规划，不再单独跑一次推演。
  const memberWaitingForHost = !isHost && roomReady && Boolean(roomCode) && hostOnline;
  const showMemberWaiting = memberWaitingForHost && !showWorkspace;

  // 等候页里点「进入规划页」：与大厅提示条走同一条路，带上房主广播的目的地。
  const enterHostPlanning = () => {
    if (roomView?.destination) {
      setDestination(roomView.destination);
      setPlanDestination(roomView.destination);
    }
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

  const handleRegenerateRoom = () => {
    if (!window.confirm('换一个房间码后，旧房间码立即失效，好友需要用新码重新加入。确定吗？')) return;
    void regenerateRoom();
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
        roomComments={roomComments}
        onNodeComment={sendNodeComment}
        onAckNodeComment={ackNodeComment}
        roomConnection={roomConnection}
        roomRetryAttempt={roomRetryAttempt}
        roomRetryDelayMs={roomRetryDelayMs}
        onRetryRoom={retryRoomConnection}
        initialDestination={planDestination}
        initialIntent={lobbyIntent}
        isHost={isHost}
        followHost={followHost}
        onFollowHostChange={setFollowHost}
        onBroadcastPlan={broadcastRoomPlan}
        onBack={handleLeaveWorkspace} 
      />
    );
  }

  if (showMemberWaiting) {
    return (
      <RoomWaitingScreen
        currentUser={currentUser}
        roomCode={roomCode}
        copied={copied}
        onCopyRoomCode={copyRoomCode}
        roomMembers={roomMembers}
        roomVotes={roomVotes}
        roomConnection={roomConnection}
        roomRetryAttempt={roomRetryAttempt}
        roomRetryDelayMs={roomRetryDelayMs}
        onRetryRoom={retryRoomConnection}
        hostName={roomFollowName}
        hostPlanning={roomView?.view === 'planning'}
        hostDestination={roomView?.destination}
        followHost={followHost}
        onFollowHostChange={setFollowHost}
        onEnterPlanning={enterHostPlanning}
      />
    );
  }

  if (showProfile) {
    return (
      <ModernProfileScreen 
        currentUser={currentUser}
        roomCode={roomCode}
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
    <div className="journey-lobby min-h-screen flex items-center justify-center p-4 sm:p-8 font-sans bg-slate-50/50 relative overflow-hidden">
      {/* 环境光球：backgroundColor 随目的地平滑过渡（成都=竹青/暖红…），极慢漂移 */}
      <div
        aria-hidden="true"
        className="ambient-orb absolute top-[-10%] left-[-10%] w-[40%] h-[40%] rounded-full blur-[120px] pointer-events-none"
        style={{ backgroundColor: (LOBBY_MOODS[destination.trim()] ?? DEFAULT_MOOD).orbA }}
      />
      <div
        aria-hidden="true"
        className="ambient-orb absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] rounded-full blur-[120px] pointer-events-none"
        style={{ backgroundColor: (LOBBY_MOODS[destination.trim()] ?? DEFAULT_MOOD).orbB, animationDelay: '-18s' }}
      />

      <AnimatePresence>
        {!isTransitioning && (
          <motion.div 
            key="lobby-main-card-wrapper"
            initial={{ opacity: 0, scale: 0.98, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.95, y: -20, filter: "blur(8px)" }} transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className="journey-lobby-sheet relative z-10 w-full max-w-[1240px] min-h-[85vh] bg-white/70 backdrop-blur-3xl border border-white shadow-[0_8px_40px_rgba(0,0,0,0.04)] rounded-[2rem] flex flex-col overflow-hidden"
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
                      {currentUser.isAdmin && (
                        <button onClick={() => { setShowMoreMenu(false); window.location.assign('/admin'); }} className="flex items-center gap-2 rounded-xl px-3 py-2 text-left text-xs font-bold text-orange-600 hover:bg-orange-50">
                          <ShieldCheck className="h-3.5 w-3.5" /> 运维看板
                        </button>
                      )}
                      <div className="mt-1 border-t border-slate-100 px-3 py-2 text-[11px] leading-relaxed text-slate-400">
                        3D 态势雷达、碳排放、AR 导览、高级信息与模型治理在规划工作区里按需展开。
                      </div>
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-2 bg-white border border-slate-200/80 px-2 py-1.5 sm:gap-3 sm:px-3 rounded-2xl shadow-xs">
                  <div className="w-8 h-8 rounded-full overflow-hidden bg-orange-100 border border-orange-200">
                    <img src={currentUser.avatarUrl ? assetUrl(currentUser.avatarUrl) : generatedAvatarUrl(currentUser.avatarSeed || currentUser.username, currentUser.nickname)} alt="avatar" className="w-full h-full object-cover" />
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
              <JourneyLobbyHero onExplore={() => setShowDestinationMap(true)} />
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
                <section className="flex flex-col gap-4 lg:col-span-8">
                  <div>
                    <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-800 tracking-tight">把下一段旅程，安排妥当。</h2>
                    <p className="mt-1 text-sm font-medium text-slate-500">
                      三步就能开始：先确定去哪、几天、几个人，再写清必须满足的条件，最后生成方案。
                    </p>
                  </div>

                  <div className="spatial-panel rounded-3xl p-5 sm:p-6">
                    <div className="mb-4 flex items-center gap-3">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-orange-500 text-xs font-black text-white shadow-sm shadow-orange-300/60">1</span>
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
                          className={`rounded-full border px-2.5 py-1 text-[11px] font-bold transition-all active:scale-95 ${destination === city ? 'border-orange-300 bg-orange-50 text-orange-700 shadow-sm shadow-orange-200/50' : 'border-slate-200 bg-white text-slate-500 hover:border-orange-200 hover:text-orange-600'}`}
                        >
                          {city}
                        </button>
                      ))}
                    </div>

                    <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div className="surface-raised rounded-2xl px-4 py-3">
                        <span className="text-xs font-bold text-slate-600">几天</span>
                        <div className="mt-2 flex items-center justify-between gap-2">
                          <button type="button" aria-label="减少天数" onClick={() => setDays((value) => Math.max(1, value - 1))} className="stepper-key h-8 w-8 shrink-0 text-sm">−</button>
                          <span data-testid="lobby-days" className="lobby-flip text-lg font-black text-slate-800 tabular-nums">
                            <AnimatePresence mode="popLayout" initial={false}>
                              <motion.span
                                key={days}
                                className="lobby-flip-card"
                                initial={reducedMotion ? false : { rotateX: -90, opacity: 0 }}
                                animate={{ rotateX: 0, opacity: 1 }}
                                exit={reducedMotion ? { opacity: 0 } : { rotateX: 90, opacity: 0 }}
                                transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
                              >
                                {days} 天
                              </motion.span>
                            </AnimatePresence>
                          </span>
                          <button type="button" aria-label="增加天数" onClick={() => setDays((value) => Math.min(15, value + 1))} className="stepper-key h-8 w-8 shrink-0 text-sm">+</button>
                        </div>
                      </div>
                      <div className="surface-raised rounded-2xl px-4 py-3">
                        <span className="flex items-center gap-1.5 text-xs font-bold text-slate-600"><Users className="h-3.5 w-3.5 text-orange-500" /> 同行人数</span>
                        <div className="mt-2 flex items-center justify-between gap-2">
                          <button type="button" aria-label="减少人数" onClick={() => setTravelers((value) => Math.max(1, value - 1))} className="stepper-key h-8 w-8 shrink-0 text-sm">−</button>
                          <span data-testid="lobby-travelers" className="lobby-flip text-lg font-black text-slate-800 tabular-nums">
                            <AnimatePresence mode="popLayout" initial={false}>
                              <motion.span
                                key={travelers}
                                className="lobby-flip-card"
                                initial={reducedMotion ? false : { rotateX: -90, opacity: 0 }}
                                animate={{ rotateX: 0, opacity: 1 }}
                                exit={reducedMotion ? { opacity: 0 } : { rotateX: 90, opacity: 0 }}
                                transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
                              >
                                {travelers} 人
                              </motion.span>
                            </AnimatePresence>
                          </span>
                          <button type="button" aria-label="增加人数" onClick={() => setTravelers((value) => Math.min(10, value + 1))} className="stepper-key h-8 w-8 shrink-0 text-sm">+</button>
                        </div>
                      </div>
                    </div>

                    <label className="mt-3 flex items-center gap-2 text-xs font-bold text-slate-600">
                      <input type="checkbox" data-testid="lobby-budget-first" checked={preferBudget} onChange={(event) => setPreferBudget(event.target.checked)} className="h-4 w-4 accent-orange-500" />
                      尽量控制预算
                    </label>

                    <div className="my-5 h-px bg-slate-100" />

                    <div className="mb-3 flex items-center gap-3">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-orange-500 text-xs font-black text-white shadow-sm shadow-orange-300/60">2</span>
                      <div>
                        <h3 className="text-sm font-extrabold text-slate-800">必须满足什么</h3>
                        <p className="text-xs font-medium text-slate-500">不写清楚，方案只能靠猜</p>
                      </div>
                    </div>
                    <div className="surface-raised flex items-start justify-between gap-3 rounded-2xl p-4">
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
                        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-orange-500 text-xs font-black text-white shadow-sm shadow-orange-300/60">3</span>
                        <div>
                          <h3 className="text-sm font-extrabold text-slate-800">生成方案</h3>
                          <p className="text-xs font-medium text-slate-500">生成后仍可修改、对比和导出</p>
                        </div>
                      </div>
                      <button
                        data-testid="begin-plan"
                        onClick={handleBeginPlan}
                        className="btn-cta-3d flex items-center justify-center gap-2 rounded-2xl px-6 py-3 text-sm font-bold text-white"
                      >
                        生成方案 <ArrowRight className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </section>

                <aside className="flex flex-col gap-4 lg:col-span-4">
                  <div className="spatial-panel relative overflow-hidden rounded-3xl p-5">
                    {/* 声呐雷达底纹：围绕房间码的静态同心圆（无持续动画，克制） */}
                    <div aria-hidden="true" className="sonar-field pointer-events-none absolute -right-12 -top-12 h-48 w-48" />
                    {/* 协同更新波纹：roomMembers 的真实 WS 变化触发一次，key 重放动画 */}
                    {roomRippleKey > 0 && (
                      <div key={roomRippleKey} aria-hidden="true" className="room-ripple pointer-events-none absolute inset-0 rounded-3xl" />
                    )}
                    <div className="relative flex flex-wrap items-center justify-between gap-2">
                      <h3 className="flex items-center gap-2 text-base font-extrabold text-slate-800"><Users className="h-4 w-4 text-orange-500" /> 加入房间</h3>
                      <div className="flex items-center gap-2">
                        <span data-testid="room-code" className="flex items-center gap-1.5 font-mono text-sm font-black tracking-wider text-orange-600">
                          {roomReady ? roomCode : '准备中…'}
                          <button onClick={copyRoomCode} className="rounded-md p-1 transition-colors hover:bg-orange-50" title="复制房间码">
                            {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5 text-orange-500" />}
                          </button>
                        </span>
                        {isHost && roomReady && (
                          <button
                            type="button"
                            onClick={handleRegenerateRoom}
                            className="flex items-center gap-1 rounded-md border border-slate-200 bg-white px-1.5 py-1 text-[10px] font-bold text-slate-500 transition-colors hover:border-orange-300 hover:text-orange-600"
                            title="换一个房间码（旧码失效）"
                          >
                            <RefreshCw className="w-3 h-3" /> 换一个
                          </button>
                        )}
                      </div>
                    </div>
                    <p className="mt-1 text-xs font-medium text-slate-500">把房间码发给同行好友，一起确认节奏、预算和必去地点。</p>
                    <div className="mt-3">
                      <RoomConnectionStatus connection={roomConnection} retryAttempt={roomRetryAttempt} retryDelayMs={roomRetryDelayMs} memberCount={roomMembers.length} onRetry={retryRoomConnection} testId="room-status-lobby-home" />
                    </div>
                    {roomAuthExpired && (
                      <div data-testid="room-auth-expired" className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5">
                        <p className="text-xs font-bold text-rose-700">登录状态已过期</p>
                        <p className="mt-1 text-[11px] font-medium text-rose-600">房间连接已停止，重新登录后会自动回到这个房间。</p>
                        <button onClick={signOut} className="mt-2 rounded-lg bg-rose-600 px-3 py-1.5 text-[11px] font-bold text-white transition-colors hover:bg-rose-700">
                          重新登录
                        </button>
                      </div>
                    )}
                    {/* 房主不在线时才会回到大厅：此时没有可等待的方案，允许自己先规划 */}
                    {!isHost && roomReady && !hostOnline && (
                      <div data-testid="room-waiting-host" className="mt-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
                        <p className="text-xs font-bold text-slate-700">房主暂时不在线</p>
                        <p className="mt-1 text-[11px] font-medium text-slate-500">
                          房主回到房间后，你可以回到等候页跟随他一起规划；现在也可以自己先生成一份。
                        </p>
                        <label className="mt-2 flex cursor-pointer items-center gap-1.5 text-[11px] font-semibold text-slate-600">
                          <input
                            type="checkbox"
                            checked={followHost}
                            onChange={(event) => setFollowHost(event.target.checked)}
                            className="h-3.5 w-3.5 rounded accent-orange-500"
                          />
                          跟随房主
                        </label>
                      </div>
                    )}
                    {/* 房主正在规划时，成员在大厅就能看到并一键进入，不用等自动跳转 */}
                    {!isHost && roomView?.view === 'planning' && (
                      <div data-testid="room-follow-banner" className="mt-3 rounded-xl border border-orange-200 bg-orange-50/70 px-3 py-2.5">
                        <p className="text-xs font-bold text-orange-700">
                          {roomFollowName} 正在规划{roomView.destination ? `「${roomView.destination}」` : ''}
                        </p>
                        <div className="mt-2 flex items-center justify-between gap-2">
                          <label className="flex cursor-pointer items-center gap-1.5 text-[11px] font-semibold text-slate-600">
                            <input
                              type="checkbox"
                              checked={followHost}
                              onChange={(event) => setFollowHost(event.target.checked)}
                              className="h-3.5 w-3.5 rounded accent-orange-500"
                            />
                            跟随房主
                          </label>
                          <button
                            onClick={() => {
                              if (roomView.destination) {
                                setDestination(roomView.destination);
                                setPlanDestination(roomView.destination);
                              }
                              setShowWorkspace(true);
                            }}
                            className="rounded-lg bg-orange-500 px-3 py-1.5 text-[11px] font-bold text-white transition-colors hover:bg-orange-600"
                          >
                            进入规划
                          </button>
                        </div>
                      </div>
                    )}
                    {roomError && <p className="mt-2 text-[11px] font-bold text-rose-500">{roomError}</p>}
                    <div className="mt-3 flex gap-1.5">
                      <input
                        data-testid="join-room-code"
                        type="text"
                        placeholder="输入好友分享的房间码"
                        value={inputRoomCode}
                        onChange={(event) => setInputRoomCode(event.target.value)}
                        className="spatial-field min-w-0 flex-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs font-bold uppercase text-slate-800 outline-none focus:border-orange-500"
                      />
                      <button data-testid="join-room-submit" onClick={joinRoom} disabled={!inputRoomCode.trim()} className="shrink-0 rounded-lg bg-slate-900 px-3.5 py-2 text-xs font-bold text-white transition-colors hover:bg-orange-500 disabled:cursor-not-allowed disabled:bg-slate-300">
                        加入
                      </button>
                    </div>
                    {roomMembers.length > 0 && (
                      <ul className="relative mt-3 space-y-1.5">
                        {roomMembers.map((member) => (
                          <li key={member.id} className="lobby-member surface-raised flex items-center justify-between rounded-xl px-2.5 py-2">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="lobby-member-ring h-7 w-7 shrink-0 overflow-hidden rounded-full border border-orange-200/70">
                                <img
                                  src={member.avatarUrl ? assetUrl(member.avatarUrl) : generatedAvatarUrl(member.avatarSeed || member.id, member.name)}
                                  alt=""
                                  className="h-full w-full object-cover"
                                  loading="lazy"
                                />
                              </span>
                              <div className="min-w-0">
                                <p className="truncate text-xs font-bold text-slate-800">{member.name}{member.id === currentUser.id ? '（我）' : ''}</p>
                                <p className="truncate text-[11px] font-medium text-slate-500">{member.role} · {member.intent}</p>
                              </div>
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
                    className="group spatial-field rounded-3xl border border-slate-200 bg-white/80 p-5 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-orange-300 hover:shadow-lg hover:shadow-orange-100/50"
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
                <button onClick={handleConfirmSync} disabled={!selectedRole || isConnecting} className={`flex-[1.5] py-3 rounded-xl font-bold flex items-center justify-center gap-2 transition-all cursor-pointer text-sm ${!selectedRole ? 'bg-slate-100 text-slate-400' : 'btn-cta-3d text-white'}`}>
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

