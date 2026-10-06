'use client';

import dynamic from 'next/dynamic';
import { useState, useRef, useEffect, useMemo } from 'react';
import { 
  Users, MapPin, Sparkles, Coffee, Camera, Car, PiggyBank, ArrowRight, Check, 
  UserPlus, Map as MapIcon, Compass, Headphones, TrendingDown, RefreshCw, 
  X, AlertCircle, Clock, ThumbsUp, ThumbsDown, BrainCircuit,
  MessageSquare, Wand2, ArrowDown, Sun, ExternalLink, Hotel, RotateCw, Split, Scale,
  Lock, User, LogIn, LogOut, Edit3, KeyRound, CheckCircle2, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, Copy,
  Compass as CompassIcon, ShieldCheck, ArrowLeft, CalendarDays, Route, Save, Shuffle,
  CalendarClock, Heart, Send, Plus, MessageCircle, Flame, Mic, Navigation,
  Trophy, Award, Medal, Star, Mountain, UtensilsCrossed, Landmark, Quote, BookOpen, Footprints,
  Activity, Radio, Wifi, Zap, Signal, Share2, Link2, Download, Train, MoreHorizontal
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { API_BASE, WS_BASE, apiFetch, apiJson, getAuthToken, installApiFetchInterceptor } from './lib/utils';
import { shortenRegionName, tryExtractJson, normalizeLnglat, extractStreamingRoutes } from './lib/lobbyUtils';
import type { Phase, RoomMember, UserProfile } from './types';

// 底部行程节点横向卡片条（自 ContextualLobby 拆分出来的展示型组件）
import BottomMapCarousel from './components/BottomMapCarousel';

// 修改诉求画像弹窗（自 ContextualLobby 拆分出来的展示型组件）
import EditIntentModal from './components/EditIntentModal';

// 多智能体推演进行中/失败面板（自 ContextualLobby 拆分出来的展示型组件）
import DeductionPanel from './components/DeductionPanel';

// 诉求打磨面板（自 ContextualLobby 拆分出来；TRAVEL_MODES/USER_ROLES 随它搬走）
import DraftingPanel from './components/DraftingPanel';

// 马蜂窝风行程面板（自 ContextualLobby 拆分出来的展示型组件）
import MafengwoStylePanel from './components/MafengwoStylePanel';

// 预算核对 / 已作调整 / 行程规模 说明面板（字段来自 final_route.payload）。
// 按需加载：只在决策阶段出现，没必要进首屏包（实测直接 import 会让首屏 JS +6.5KB）。
const PlanGovernancePanel = dynamic(() => import('./components/PlanGovernancePanel'), { ssr: false });
import type { PlanGovernance } from './components/PlanGovernancePanel';
import NegotiationDiagnostics from './components/NegotiationDiagnostics';
import PlanFactsPanel from './components/PlanFactsPanel';

// 系统图标与登录门户（自 ContextualLobby 拆分出来的展示型组件）
import OmniLogo from './components/OmniLogo';
import AuthPortalScreen from './components/AuthPortalScreen';

// 个人主页与社区组件
import ModernProfileScreen from './components/ProfileScreen';
import CommunityPanel from './components/CommunityPanel';

// 一键分享行程海报
import ItineraryPoster from './components/ItineraryPoster';

// 旅中风险推送中心：轮询实时风险快照，检测天气/路况/安全事件变更并推送
import RiskPushCenter from './components/RiskPushCenter';

// 碳足迹与绿色交通评估（P5）
import CarbonFootprintPanel from './components/CarbonFootprintPanel';
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
import RiskPerturbationPanel from './components/RiskPerturbationPanel';
import ReferenceRoutePanel from './components/ReferenceRoutePanel';
import { normalizeRiskAlert, type RiskAlertView } from './lib/riskPerturbation';

type WorkspaceLayer = 'plan' | 'evidence' | 'execution' | 'professional';

// 目的地图谱数据（用于主页灵感目的地推荐）
import { PROVINCE_DATA } from './data/provinceData';

// 主控诉求与企业选择卡（自 ContextualLobby 拆分出来的展示型组件）
import { TravelModeCard, PreferenceCard } from './components/SelectCards';

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
              <h2 className="text-xl font-bold text-slate-800 mb-1 flex items-center gap-2"><TrendingDown className="w-5 h-5 text-orange-600"/> 极客精算中枢</h2>
              <p className="text-xs text-slate-500 font-medium">底层算力全开，为您挖掘全网极致性价比组合</p>
            </div>
            <div className="p-6 flex-1 flex flex-col gap-4">
              <div className="p-4 rounded-xl bg-gradient-to-br from-orange-50/50 to-white border border-orange-100 shadow-sm flex items-center justify-between">
                <div>
                  <p className="text-xs font-bold text-slate-400 mb-1">当前测算状态</p>
                  <p className="text-sm font-bold text-slate-800 flex items-center gap-2">
                    <TrendingDown className="w-4 h-4 text-orange-500" /> 全网比价引擎就绪，进入推演后自动拉取
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
                <span className="text-[10px] bg-stone-100 text-stone-500 font-bold px-2 py-0.5 rounded-full border border-stone-200">WebSocket 实时协同</span>
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
                    <img src={currentUser.avatarUrl ? `${API_BASE}${currentUser.avatarUrl}` : `https://api.dicebear.com/9.x/avataaars/svg?seed=${currentUser.avatarSeed}&backgroundColor=fdeed8`} alt="avatar" className="w-full h-full object-cover" />
                  </div>
                  <div className="text-left">
                    <span className="text-xs font-black text-slate-800 block">{currentUser.nickname}</span>
                    <span className="text-[10px] text-stone-400 font-bold">SQLite 认证就绪</span>
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
                      {selectedMode === 'coop' && '什么是“基于房间的多智能体协同”？'}
                      {selectedMode === 'pvp' && "极致性价比是如何做到的？"}
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
              <div onClick={() => setShowDestinationMap(true)} className="group bg-white/60 backdrop-blur-md border border-slate-200/70 rounded-2xl p-5 cursor-pointer hover:border-orange-300 hover:shadow-lg hover:shadow-orange-100/50 transition-all">
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
                  <BrainCircuit className="w-4 h-4 text-orange-600" />
                  <h3 className="text-sm font-extrabold text-slate-800">智能体进化日志</h3>
                </div>
                {[
                  '融合全网评价与真实反馈，路线持续自我修正',
                  '社区高质量行程作为软引导，辅助共识推演',
                  '帕累托博弈在每一轮协商中自适应校准'
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

// ================= 工作区区域 =================
function UnifiedWorkspace({ mode, role, roomCode, roomMembers, currentUser, initialDestination, onBack }: { mode: string, role: string, roomCode: string, roomMembers: RoomMember[], currentUser: UserProfile, initialDestination?: string | null, onBack: () => void }) {
  const [phase, setPhase] = useState<Phase>('drafting');
  // Keep the decision workspace focused. Advanced evidence and diagnostics are
  // still available, but are no longer rendered as a wall of panels by default.
  const [workspaceLayer, setWorkspaceLayer] = useState<WorkspaceLayer>('plan');
  const [showWorkspaceMenu, setShowWorkspaceMenu] = useState(false);
  const [agentCapabilities, setAgentCapabilities] = useState<any[]>([]);
  const [agentEvaluation, setAgentEvaluation] = useState<Record<string, any>>({});
  const [providerRegistry, setProviderRegistry] = useState<any[]>([]);
  const [providerQuality, setProviderQuality] = useState<any | null>(null);
  const [providerQualityTrend, setProviderQualityTrend] = useState<any | null>(null);
  const [channelMetrics, setChannelMetrics] = useState<any | null>(null);
  const [featureSnapshot, setFeatureSnapshot] = useState<any | null>(null);
  const [qualitySummary, setQualitySummary] = useState<any | null>(null);
  const [runtimeHealth, setRuntimeHealth] = useState<any | null>(null);
  const [evaluationAudit, setEvaluationAudit] = useState<any | null>(null);
  const [auditCleanupHealth, setAuditCleanupHealth] = useState<any | null>(null);
  const [auditSummary, setAuditSummary] = useState<any | null>(null);
  const [auditComparison, setAuditComparison] = useState<any | null>(null);
  const [auditVersionFilter, setAuditVersionFilter] = useState('');
  const [auditSinceFilter, setAuditSinceFilter] = useState('');
  const [auditLoadingMore, setAuditLoadingMore] = useState(false);
  const [agentDiagnosticsLoading, setAgentDiagnosticsLoading] = useState(false);
  const [savedTripId, setSavedTripId] = useState('');
  const planningResourceId = savedTripId || roomCode;
  useEffect(() => {
    if (phase !== 'decision') setWorkspaceLayer('plan');
  }, [phase]);
  useEffect(() => {
    if (phase !== 'decision' || workspaceLayer !== 'professional') return;
    let active = true;
    setAgentDiagnosticsLoading(true);
    Promise.all([
      apiJson<any>(`${API_BASE}/api/v1/planning/agents/capabilities`),
      apiJson<any>(`${API_BASE}/api/v1/planning/agents/evaluation?trip_id=${encodeURIComponent(planningResourceId)}`),
      apiJson<any>(`${API_BASE}/api/v1/planning/providers/registry`),
      apiJson<any>(`${API_BASE}/api/v1/planning/providers/quality?window_days=7`),
      apiJson<any>(`${API_BASE}/api/v1/planning/providers/quality/trend?window_days=14`),
      apiJson<any>(`${API_BASE}/api/v1/planning/features?trip_id=${encodeURIComponent(planningResourceId)}`),
      apiJson<any>(`${API_BASE}/api/v1/planning/quality?trip_id=${encodeURIComponent(planningResourceId)}`),
      apiJson<any>(`${API_BASE}/api/v1/planning/channels/metrics?trip_id=${encodeURIComponent(planningResourceId)}`),
      apiJson<any>(`${API_BASE}/api/v1/planning/models/runtime-health`),
      apiJson<any>(`${API_BASE}/api/v1/planning/models/evaluation-audit?limit=8${auditVersionFilter ? `&version=${encodeURIComponent(auditVersionFilter)}` : ''}${auditSinceFilter ? `&since=${encodeURIComponent(new Date(`${auditSinceFilter}T00:00:00Z`).toISOString())}` : ''}`),
      apiJson<any>(`${API_BASE}/api/v1/planning/models/audit-cleanup-health`),
      apiJson<any>(`${API_BASE}/api/v1/planning/models/evaluation-audit/summary?${auditVersionFilter ? `version=${encodeURIComponent(auditVersionFilter)}&` : ''}${auditSinceFilter ? `since=${encodeURIComponent(new Date(`${auditSinceFilter}T00:00:00Z`).toISOString())}` : ''}`),
      apiJson<any>(`${API_BASE}/api/v1/planning/models/evaluation-audit/compare${auditSinceFilter ? `?since=${encodeURIComponent(new Date(`${auditSinceFilter}T00:00:00Z`).toISOString())}` : ''}`),
    ]).then(([capabilityPayload, evaluationPayload, registryPayload, providerQualityPayload, providerQualityTrendPayload, featurePayload, qualityPayload, channelMetricsPayload, runtimeHealthPayload, evaluationAuditPayload, auditCleanupPayload, auditSummaryPayload, auditComparisonPayload]) => {
      if (!active) return;
      const capabilities = capabilityPayload?.data?.agents || capabilityPayload?.agents;
      const evaluation = evaluationPayload?.data?.agents || evaluationPayload?.agents;
      setAgentCapabilities(Array.isArray(capabilities) ? capabilities : []);
      setAgentEvaluation(evaluation && typeof evaluation === 'object' ? evaluation : {});
      const registry = registryPayload?.data?.providers || registryPayload?.providers;
      setProviderRegistry(Array.isArray(registry) ? registry : []);
      setProviderQuality(providerQualityPayload?.data || providerQualityPayload || null);
      setProviderQualityTrend(providerQualityTrendPayload?.data || providerQualityTrendPayload || null);
      setFeatureSnapshot(featurePayload?.data || featurePayload || null);
      setQualitySummary(qualityPayload?.data || qualityPayload || null);
      setChannelMetrics(channelMetricsPayload?.data || channelMetricsPayload || null);
      setRuntimeHealth(runtimeHealthPayload?.data || runtimeHealthPayload || null);
      setEvaluationAudit(evaluationAuditPayload?.data || evaluationAuditPayload || null);
      setAuditCleanupHealth(auditCleanupPayload?.data || auditCleanupPayload || null);
      setAuditSummary(auditSummaryPayload?.data || auditSummaryPayload || null);
      setAuditComparison(auditComparisonPayload?.data || auditComparisonPayload || null);
    }).catch(() => {
      if (!active) return;
      setAgentCapabilities([]);
      setAgentEvaluation({});
      setProviderRegistry([]);
      setProviderQuality(null);
      setProviderQualityTrend(null);
      setFeatureSnapshot(null);
      setQualitySummary(null);
      setChannelMetrics(null);
      setRuntimeHealth(null);
      setEvaluationAudit(null);
      setAuditCleanupHealth(null);
      setAuditSummary(null);
      setAuditComparison(null);
    }).finally(() => {
      if (active) setAgentDiagnosticsLoading(false);
    });
    return () => { active = false; };
  }, [phase, workspaceLayer, planningResourceId, auditVersionFilter, auditSinceFilter]);
  const loadEarlierAuditEvents = async () => {
    const cursor = evaluationAudit?.pagination?.next_cursor;
    if (!cursor || auditLoadingMore) return;
    setAuditLoadingMore(true);
    try {
      const query = `${API_BASE}/api/v1/planning/models/evaluation-audit?limit=8&cursor=${encodeURIComponent(cursor)}${auditVersionFilter ? `&version=${encodeURIComponent(auditVersionFilter)}` : ''}${auditSinceFilter ? `&since=${encodeURIComponent(new Date(`${auditSinceFilter}T00:00:00Z`).toISOString())}` : ''}`;
      const payload = await apiJson<any>(query);
      const next = payload?.data || payload;
      if (!next || typeof next !== 'object') return;
      setEvaluationAudit((previous: any) => ({
        ...next,
        timeline: [...(Array.isArray(previous?.timeline) ? previous.timeline : []), ...(Array.isArray(next.timeline) ? next.timeline : [])],
      }));
    } catch {
      // The existing page remains visible when a later audit page is unavailable.
    } finally {
      setAuditLoadingMore(false);
    }
  };
  const [intentsReady, setIntentsReady] = useState(false);
  const [showBlackboard, setShowBlackboard] = useState(false);
  const [tripSaved, setTripSaved] = useState(false);
  // Use the live room as the resource key while drafting, then switch to the
  // persisted trip ID after saving. City names are display data, not IDs.

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
  const [fairnessAudit, setFairnessAudit] = useState<any>(null);
  const [agentContractAudits, setAgentContractAudits] = useState<any[]>([]);
  const [specialistRuntime, setSpecialistRuntime] = useState<any | null>(null);
  const [optimizationAudit, setOptimizationAudit] = useState<any | null>(null);
  // 概率化数字孪生结果（P50/P90 时长、超预算概率、成员满意度分布）。
  // 这些是采样得到的分布，而不是单点估计，因此可用于表达不确定度。
  const [simulation, setSimulation] = useState<{
    total_minutes?: { p50?: number; p90?: number; mean?: number };
    cost?: { known_p50?: number; known_p90?: number; complete_probability?: number };
    budget_overrun_probability?: number | null;
    per_day_minutes_p90?: Record<string, number>;
    member_satisfaction?: Record<string, { satisfaction_p50?: number; satisfaction_floor_p10?: number }>;
    assumptions?: { unpriced_nodes?: number; defaulted_dwell?: number; defaulted_travel?: number; sample_count?: number };
  } | null>(null);
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
  const decisionEventRecordedRef = useRef(false);
  const lastTokenTimeRef = useRef<number>(Date.now());
  const [wsConnected, setWsConnected] = useState(false);
  const [deductionTimeout, setDeductionTimeout] = useState(false);
  const [deductionError, setDeductionError] = useState<string | null>(null);
  const [isFallbackRoute, setIsFallbackRoute] = useState(false);
  // 工具栏「更多」折叠（原来 10 个按钮挤成一团）
  const [showMoreTools, setShowMoreTools] = useState(false);
  // 专业诊断层：高级诊断（技术指标）与智能体运行明细默认收起，避免一屏堆满术语
  const [showAdvancedDiagnostics, setShowAdvancedDiagnostics] = useState(false);
  const [showAgentRuntimeDetail, setShowAgentRuntimeDetail] = useState(false);
  // 规划核对信息（预算三级模型 / 兜底调整说明 / 超长行程建议 / 门禁问题），随 final_route 下发
  const [planGovernance, setPlanGovernance] = useState<PlanGovernance | null>(null);
  // 本次规划的关联键（ai-service 在 final_route 里回填 correlation_id）。
  // 存进行程内容后，网关侧的"采纳 / 到访 / 重规划确认"事件就能与推演事件归到同一条轨迹；
  // 不存的话网关只能退回 trip_id，跨服务轨迹就串不起来（事件里会标 correlation_source=trip_id）。
  const [planCorrelationId, setPlanCorrelationId] = useState('');
  // 旅中风险告警（gateway 广播 risk_subscription_alert，可带最小扰动范围）：
  // 只提示与提案，**用户确认前不改动现有方案**
  const [riskAlert, setRiskAlert] = useState<RiskAlertView | null>(null);
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
    category: r.planning_category || r.category || r.type || "other",
    cost: r.cost_estimate || r.cost || "暂无供应商数据",
    cost_estimate: r.cost_estimate ?? r.cost ?? null,
    data_sources: r.data_sources || {},
    estimated: Boolean(r.estimated),
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

    // 规划核对面板的数据来源只有一个：后端 final_route.payload。
    // `stream_token` 里的 [FINAL_JSON] 与 `final_route` 消息带的是同一份 payload，
    // 两条路径都要写，缺一条就会出现"方案出来了但核对面板是空的"。
    const applyPlanGovernance = (fd: any) => {
      if (!fd || typeof fd !== 'object') return;
      // 关联键要在"有没有治理数据"的判断之前抓：保底路线那条 final_route 不带 quality 等字段，
      // 但它同样属于这次规划，不能因此丢掉关联键。
      if (typeof fd.correlation_id === 'string' && fd.correlation_id.trim()) {
        setPlanCorrelationId(fd.correlation_id.trim());
      }
      // 只合并、不整体替换：后端在**保底模式**下额外下发的那条 final_route 只带
      // status/route/simulation，不带 quality/budget_report…；早先这里整体覆盖，
      // 结果那条消息把刚拿到的核对数据（预算/门禁/诉求/降级）全清空、面板整块消失。
      const hasGovernance = Boolean(
        fd.quality || fd.budget_report || fd.fallback || fd.horizon ||
          fd.increment || fd.price_audit || fd.transport_audit || fd.long_trip ||
          fd.review || fd.constraints || fd.degradations || fd.daily_balance ||
          fd.lodging_joint || fd.reference_profile || fd.candidate_supply || fd.composition || fd.negotiation || fd.facts ||
          fd.feedback_applied || fd.lodging_supply,
      );
      if (!hasGovernance) return;
      setPlanGovernance((prev) => ({
        quality: fd.quality ?? prev?.quality,
        budget: fd.budget_report ?? prev?.budget,
        fallback: fd.fallback ?? prev?.fallback,
        horizon: fd.horizon ?? prev?.horizon,
        dailyBalance: fd.daily_balance ?? prev?.dailyBalance,
        // 二次增量（这次到底改成了什么）、价格核对、跨城出行比较、长途分段、自动复核、
        // 诉求核对、数据降级：七块都是**后端说了才显示**，缺字段时面板不渲染空壳。
        increment: fd.increment ?? prev?.increment,
        priceAudit: fd.price_audit ?? prev?.priceAudit,
        transportAudit: fd.transport_audit ?? prev?.transportAudit,
        longTrip: fd.long_trip ?? prev?.longTrip,
        review: fd.review ?? prev?.review,
        constraints: fd.constraints ?? prev?.constraints,
        degradations: fd.degradations ?? prev?.degradations,
        // 住哪的联合评估：房价之外的连锁影响（通勤 / 交通费 / 体力 / 碳排 / 可玩节点 / 预算余额）
        lodgingJoint: fd.lodging_joint ?? prev?.lodgingJoint,
        // 本次是否按"参考路线画像"排的（后端只在诉求没说明白时才用，并回填依据）
        referenceProfile: fd.reference_profile ?? prev?.referenceProfile,
        // 本次真实候选够不够（景点/餐饮各多少、有没有补检索、哪些地方靠模型知识补）
        candidateSupply: fd.candidate_supply ?? prev?.candidateSupply,
        // 本次配比（目标 vs 实际）：默认自然=人文各半并穿插美食住宿；自然只算景区
        composition: fd.composition ?? prev?.composition,
        // 本次协商纪要：谁参与、站在什么立场、争了什么、裁决凭什么
        negotiation: fd.negotiation ?? prev?.negotiation,
        // 统一事实记录：请求/约束/供应商/决策/结果/缺口，每条带来源与核实状态
        facts: fd.facts ?? prev?.facts,
        // 节点级负反馈的落地情况：你点过"不喜欢"的地点/原因，这次到底改了没有
        // （后端算出来了但界面不渲染 = 用户以为点了没用）
        feedbackApplied: fd.feedback_applied ?? prev?.feedbackApplied,
        // 住宿候选的供应商实价清单：同样的预算还有哪些有实价的可选（用户的选择权）
        lodgingSupply: fd.lodging_supply ?? prev?.lodgingSupply,
      }));
    };

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
                // 👑 流式原文**原样累积**：以前在累积阶段就把 "null" 全部删掉，而合法 JSON 里的
                // null（例如票价未核实时 price:null）会被删成 "price":，整段 JSON 直接解析失败
                // —— 后果是 final_route 里的 quality / budget_report 全部丢失，核对面板不出现。
                // 现在先按原文解析（我们自己下发的 JSON 一定合法），解析不出来才退回"删 null"的
                // 兜底（那是为模型吐字里夹带的 null 准备的）。展示用的清洗在 humanReadableLogs。
                const newText = (prev || "") + rawToken;

                const finalData = tryExtractJson(newText) || tryExtractJson(newText.replace(/null/g, ""));
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
                    category: r.planning_category || r.category || r.type || "other",
                    cost: r.cost_estimate || r.cost || "暂无供应商数据",
                    cost_estimate: r.cost_estimate ?? r.cost ?? null,
                    data_sources: r.data_sources || {},
                    estimated: Boolean(r.estimated),
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
                  // 👑 多方案：主方案 + 后端差异化方案。
                  // 流式路径（stream_token → [FINAL_JSON]）是正常到达方式，必须与 final_route
                  // 分支同样写入 planVariants；此前只有 final_route 分支写，导致正常出方案时
                  // 裁决看板的「保留方案 / 替代方案」两列永远是空的，只剩一套方案。
                  const streamVariants = Array.isArray(finalData.plan_variants)
                    ? finalData.plan_variants.map((v: any) => ({ id: v.id, name: v.name, style: v.style, desc: v.desc, nodes: mapRouteForDynamic(v.route) })).filter((v: any) => Array.isArray(v.nodes) && v.nodes.length > 0)
                    : [];
                  setPlanVariants([{ id: 'primary', name: 'AI 主方案', style: 'primary', desc: '', nodes: mappedRoutes }, ...streamVariants]);
                  setActivePlanId('primary');
                  // 流式路径也认一次"保底路线"：后端在保底模式下会额外下发 final_route，
                  // 但只收到 stream_token（或它先到）时这个标记也必须立起来，不能等。
                  setIsFallbackRoute(finalData.status === 'degraded_fallback');
                  if (finalData.negotiation_summary) setConsensusSummary(finalData.negotiation_summary);
                  if (finalData.team_satisfaction) setTeamSatisfaction(finalData.team_satisfaction);
                  // 概率化仿真的分布结果（P50/P90 与超预算概率）
                  if (finalData.simulation && typeof finalData.simulation === 'object') {
                    setSimulation(finalData.simulation);
                  } else {
                    setSimulation(null);
                  }
                  // 规划核对：预算三级模型 + 兜底调整说明 + 超长行程建议 + 门禁问题。
                  // 后端可能只给其中一部分（例如未超预算时没有 fallback），缺什么就渲染什么。
                  applyPlanGovernance(finalData);
                  if (finalData.arbitration_records) setArbitrationRecords(finalData.arbitration_records);
                  if (finalData.fairness && typeof finalData.fairness === 'object') setFairnessAudit(finalData.fairness);
                  if (Array.isArray(finalData.agent_contracts)) setAgentContractAudits(finalData.agent_contracts);
                  if (finalData.specialist_runtime && typeof finalData.specialist_runtime === 'object') setSpecialistRuntime(finalData.specialist_runtime);
                  if (finalData.optimization_audit && typeof finalData.optimization_audit === 'object') setOptimizationAudit(finalData.optimization_audit);
                  recordDecisionAudit(finalData);
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
            } else if (msg.type === "risk_subscription_alert") {
              // 旅中风险：gateway 向房间广播风险变化，可能附带"最小扰动范围"提案。
              // 解析交给 lib/riskPerturbation（纯函数、有单测）：算不出来就说算不出来，
              // 绝不在这里凭空推断"影响哪几个节点"。有可执行范围时才把用户带到旅中执行层，
              // 否则提示留在原地（避免无谓跳层打断阅读）。
              const alert = normalizeRiskAlert(msg.payload);
              if (alert) {
                setRiskAlert(alert);
                if (alert.perturbation) setWorkspaceLayer('execution');
              }
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
                  category: r.planning_category || r.category || r.type || "other",
                  cost: r.cost_estimate || r.cost || "暂无供应商数据",
                  cost_estimate: r.cost_estimate ?? r.cost ?? null,
                  data_sources: r.data_sources || {},
                  estimated: Boolean(r.estimated),
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
              if (fd.fairness && typeof fd.fairness === 'object') setFairnessAudit(fd.fairness);
              if (Array.isArray(fd.agent_contracts)) setAgentContractAudits(fd.agent_contracts);
              if (fd.specialist_runtime && typeof fd.specialist_runtime === 'object') setSpecialistRuntime(fd.specialist_runtime);
              if (fd.optimization_audit && typeof fd.optimization_audit === 'object') setOptimizationAudit(fd.optimization_audit);
              recordDecisionAudit(fd);
              // final_route 是权威 payload：只收到它（重连、回放、流式 token 丢了）时
              // 核对面板同样要有数据。
              applyPlanGovernance(fd);
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
    setSpecialistRuntime(null);
    setOptimizationAudit(null);
    lastTokenTimeRef.current = Date.now();
    autoSavedRef.current = false;
    decisionEventRecordedRef.current = false;

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
    setSpecialistRuntime(null);
    setOptimizationAudit(null);
    setStreamedText("");
  };

  const recordDecisionAudit = (data: any) => {
    if (decisionEventRecordedRef.current || !planningResourceId || !data || typeof data !== 'object') return;
    decisionEventRecordedRef.current = true;
    const fairness = data.fairness && typeof data.fairness === 'object' ? data.fairness : {};
    const pareto = data.optimization_audit && typeof data.optimization_audit === 'object' ? data.optimization_audit : {};
    const runtime = data.specialist_runtime && typeof data.specialist_runtime === 'object' ? data.specialist_runtime : {};
    void apiFetch(`${API_BASE}/api/v1/planning/events`, {
      method: 'POST',
      body: JSON.stringify({
        trip_id: planningResourceId,
        event_type: 'planning_decision_audit',
        payload: {
          module: 'joint',
          model_version: data.model?.version || data.model_version || 'ai-service',
          candidate_count: Number(pareto.frontier_count || 0) + Number(pareto.rejected_count || 0),
          pareto_frontier_count: Number(pareto.frontier_count || 0),
          pareto_rejected_count: Number(pareto.rejected_count || 0),
          pareto_rejected_reasons: Array.isArray(pareto.rejected) ? pareto.rejected.slice(0, 20).map((item: any) => ({ reason: item?.reason || 'unknown', candidate: item?.candidate || '' })) : [],
          fairness_minimum_utility: Number(fairness.minimum_utility || 0),
          fairness_utility_stddev: Number(fairness.utility_stddev || 0),
          fairness_threshold: Number(fairness.threshold || 0.15),
          fairness_threshold_exceeded: Boolean(fairness.threshold_exceeded),
          fairness_repair_status: fairness.repair_status || 'not_needed',
          specialist_completed: Number(runtime.completed || 0),
          specialist_degraded: Number(runtime.degraded || 0),
          specialist_gate_audits: runtime.gate_audits || {},
        },
      }),
    }).catch(() => {
      decisionEventRecordedRef.current = false;
    });
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
      routes: routesToSave,
      // 带上关联键：网关侧事件（采纳/到访/重规划）才能与 ai-service 的推演事件串成一条轨迹
      ...(planCorrelationId ? { correlation_id: planCorrelationId } : {}),
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
    const saved = await persistTripRoutes(dynamicRoutes);
    if (!saved) return;
    // 「保存到我的行程」= 用户明确选定了当前这套方案 → 记一次采纳（事件流里的 user.accepted）。
    // 下面的**自动保存**刻意不走这里：那只是系统落库，不代表用户做了选择，
    // 否则方案一生成就"被采纳"，是假信号。
    // recordPlanAcceptance 按「方案名 + 风格」去重，重复点保存不会重复上报。
    const activeVariant = planVariants.find((v: any) => v.id === activePlanId) || planVariants[0];
    if (activeVariant) await recordPlanAcceptance({ ...activeVariant, nodes: dynamicRoutes });
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
      // The gateway is the single commit point. The browser only previews and
      // validates candidates; it must not overwrite the trip or emit an
      // accepted/applied event before the server confirms the transaction.
      const confirmResponse = await apiFetch(`${API_BASE}/api/v1/planning/replan/confirm`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          trip_id: planningResourceId,
          proposal_id: replanProposal.proposalId || '',
          affected_nodes: affected,
          candidate,
          confirmation: 'user_confirmed',
        }),
      });
      const confirmPayload = await confirmResponse.json();
      if (!confirmResponse.ok) throw new Error(confirmPayload?.error?.message || confirmPayload?.error || '服务端未确认重规划');
      const confirmed = confirmPayload?.data || confirmPayload;
      const nextRoutes = Array.isArray(confirmed?.route) ? confirmed.route : [...dynamicRoutes];
      setDynamicRoutes(nextRoutes);
      setReplanProposal(null);
      setApiError(`应急替代节点已由服务端确认并保存，保留 ${confirmed?.preserved_nodes ?? Math.max(0, dynamicRoutes.length - affected.length)} 个未受影响节点`);
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
      await apiFetch(`${API_BASE}/api/v1/planning/replan/reject`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: planningResourceId, proposal_id: replanProposal.proposalId || '', reason: 'keep_original' }),
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

  // 👑 自动保存到“我的路书”：首次生成完整路线后静默落库，无需手动点击。
  // 注意：这里直接调 persistTripRoutes 而**不是** handleSaveTrip —— 自动落库不等于"用户采纳"，
  // 采纳只在用户真的点了「保存到我的行程」或切换方案时才记录。
  useEffect(() => {
    if (phase === 'decision' && dynamicRoutes.length > 0 && !autoSavedRef.current) {
      autoSavedRef.current = true;
      void persistTripRoutes(dynamicRoutes);
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
  // 用户"采纳方案"是真实决策信号（事件流里的 user.accepted）。这里只在**用户明确切换方案**时上报：
  // 自动保存路径不报（否则一生成方案就"被采纳"，那是假的）。同一套方案在一次会话里只报一次。
  // 上报失败只记日志：审计是旁路，绝不能因为上报失败影响用户切方案。
  const recordedAcceptanceRef = useRef<Set<string>>(new Set());
  const recordPlanAcceptance = async (variant: any) => {
    if (!variant || !currentUser?.id || !roomCode) return;
    const key = `${variant.name || ''}|${variant.style || ''}`;
    if (recordedAcceptanceRef.current.has(key)) return;
    recordedAcceptanceRef.current.add(key);
    try {
      const route = JSON.stringify(Array.isArray(variant.nodes) ? variant.nodes : []);
      const saved = await fetch(`${API_BASE}/api/v1/plan/variant`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAuthToken()}` },
        body: JSON.stringify({ room_id: roomCode, name: variant.name || '未命名方案', style: variant.style || '', route }),
      });
      const savedPayload = await saved.json().catch(() => ({}));
      const variantId = String(savedPayload?.variant?.id || '');
      if (!variantId) return;
      await fetch(`${API_BASE}/api/v1/plan/variant/adopt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAuthToken()}` },
        body: JSON.stringify({ variant_id: variantId, user_id: currentUser.id, trip_id: savedTripId || undefined }),
      });
    } catch (error) {
      console.warn('采纳信号上报失败（不影响使用）', error);
    }
  };

  const handleSelectPlan = (id: string) => {
    const v = planVariants.find((x: any) => x.id === id);
    if (v && Array.isArray(v.nodes) && v.nodes.length > 0) {
      setDynamicRoutes(v.nodes);
      setActivePlanId(id);
      setActiveDayIndex(0);
      setSelectedPoiIndex(0);
      setPlanSwitchNotice(`已切换至「${v.name || '新方案'}」：${v.desc || '路线节点与节奏已更新'}`);
      void recordPlanAcceptance(v);
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

  const runtimeModels = Array.isArray(runtimeHealth?.models) ? runtimeHealth.models : [];
  const runtimeStatusLabel = (status: string) => status === 'healthy' ? '运行正常' : status === 'rollback_required' ? '已触发回滚' : '样本不足';

  return (
    <motion.div key="workspace-root" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} className="h-screen w-full bg-white flex flex-col lg:flex-row overflow-y-auto lg:overflow-hidden font-sans text-slate-800">
      
      {/* 左侧长图文游记与路径排版区 */}
      <div className="w-full lg:w-[620px] xl:w-[700px] h-[56vh] lg:h-full min-h-[480px] lg:min-h-0 flex flex-col relative z-20 border-b lg:border-b-0 lg:border-r border-slate-200 bg-white shadow-2xl shrink-0">
        <header className="px-5 sm:px-8 py-4 border-b border-slate-100 bg-white shadow-xs z-10">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-2xl font-black text-slate-800 tracking-tight">{targetCityInfo ? targetCityInfo.name + '全景路线规划' : '规划您的行程'}</h2>
            <p className="text-xs text-slate-400 font-semibold mt-0.5">路线、预算与出行依据集中在这里</p>
          </div>
          {phase === 'decision' && (
            // 工具栏折叠：常用的 4 个留在外面，其余收进「更多」。原来 10 个按钮挤成 3 列，又长又乱。
            <div className="workspace-toolbar relative flex w-full flex-wrap items-center gap-2 sm:w-[430px]" aria-label="行程工具">
              {/* 主行：点其中任何一个都把「更多」菜单收起来（菜单不该一直挂在屏幕上） */}
              <div className="flex flex-wrap items-center gap-2" onClick={() => setShowMoreTools(false)}>
              <button data-testid="save-trip" onClick={handleSaveTrip} className={`px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer ${tripSaved ? 'btn-success' : ''}`}>
                {tripSaved ? <Check className="w-3.5 h-3.5" /> : <Save className="w-3.5 h-3.5" />} {tripSaved ? '已保存' : '保存到我的行程'}
              </button>
              <button data-testid="open-diary" onClick={() => setShowDiary(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"><BookOpen className="w-3.5 h-3.5"/> 行程日记</button>
              <button data-testid="export-ics" onClick={downloadIcs} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"><Download className="w-3.5 h-3.5"/> 导出日历</button>
              <button onClick={() => setShowBudget(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="打开预算管家"><PiggyBank className="w-3.5 h-3.5"/> 预算账本</button>
              </div>
              <button
                data-testid="toggle-more-tools"
                onClick={() => setShowMoreTools((open) => !open)}
                aria-expanded={showMoreTools}
                className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"
              >
                <MoreHorizontal className="w-3.5 h-3.5" /> {showMoreTools ? '收起' : '更多'}
              </button>
              {showMoreTools && (
                <div
                  // 点菜单里任何一项都收起（捕获阶段先关，再执行按钮自己的动作）
                  onClick={() => setShowMoreTools(false)}
                  className="absolute right-0 top-full z-50 mt-2 grid w-[330px] grid-cols-2 gap-2 rounded-2xl border border-stone-200 bg-white/98 p-3 shadow-xl backdrop-blur"
                >
                  <button onClick={handleRegenerateVariant} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer">
                    <Shuffle className="w-3.5 h-3.5"/> 换一版
                  </button>
                  <button onClick={() => setShowPoster(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer">
                    <Share2 className="w-3.5 h-3.5"/> 行程海报
                  </button>
                  <button data-testid="export-json" onClick={downloadJson} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer"><Download className="w-3.5 h-3.5"/> 导出 JSON</button>
                  <button onClick={() => setShowExpenseReport(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="查看消费复盘"><TrendingDown className="w-3.5 h-3.5"/> 消费复盘</button>
                  <button onClick={() => setShowSatisfaction(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="提交行程满意度"><Star className="w-3.5 h-3.5"/> 行程评价</button>
                  <button onClick={() => setShowBookingHub(true)} className="px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center gap-1.5 border shadow-2xs cursor-pointer" title="打开官方渠道"><ExternalLink className="w-3.5 h-3.5"/> 官方渠道</button>
                  <button onClick={() => { setPhase('drafting'); setIntentsReady(false); setShowMoreTools(false); }} className="btn-accent col-span-2 px-3.5 py-1.5 rounded-xl font-bold text-xs transition-colors flex items-center justify-center gap-1.5 border shadow-2xs cursor-pointer">
                    <Wand2 className="w-3.5 h-3.5"/> 重新调整
                  </button>
                </div>
              )}
            </div>
          )}
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2" aria-label="规划进度">
            {([
              ['drafting', '01', '描述需求'],
              ['deduction', '02', '生成方案'],
              ['decision', '03', '确认行程'],
            ] as const).map(([key, index, label]) => {
              const active = phase === key;
              const complete = (key === 'drafting' && phase !== 'drafting') || (key === 'deduction' && phase === 'decision');
              return <div key={key} className={`relative flex items-center gap-2 rounded-xl px-3 py-2 border transition-colors ${active ? 'bg-orange-50 border-orange-200 text-orange-700' : complete ? 'bg-stone-100 border-stone-200 text-stone-500' : 'bg-slate-50 border-slate-100 text-slate-400'}`}>
                <span className="font-mono text-[10px] font-black">{complete ? '✓' : index}</span><span className="text-[11px] font-bold truncate">{label}</span>
              </div>;
            })}
          </div>
          {phase === 'decision' && (
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-bold text-slate-500" aria-label="路书质量摘要">
              <span><strong className="text-slate-800">{planQuality.days}</strong> 天 · <strong className="text-slate-800">{planQuality.nodes}</strong> 个节点</span>
              {/* 花费只认一个来源：后端给了三级预算报告（已核实/估算/未核实）时，
                  这里不再重复显示前端自行汇总的"估算总额"，避免同屏出现两个不同金额。 */}
              {!planGovernance?.budget && (
                planQuality.costUnavailable ? (
                  <span className="text-amber-700" title="供应商未返回价格，无法给出估算总额">
                    价格待补充<strong className="ml-1">（{planQuality.unpricedCount} 个节点无报价）</strong>
                  </span>
                ) : (
                  <span>
                    估算 ¥<strong className="text-orange-700">{planQuality.estimatedCost.toLocaleString()}</strong>
                    {planQuality.unpricedCount > 0 && (
                      <span className="ml-1 font-medium text-slate-400" title="这些节点缺少供应商报价，未计入合计">
                        · {planQuality.unpricedCount} 个未计价
                      </span>
                    )}
                  </span>
                )
              )}
              <span>坐标覆盖 <strong className={planQuality.coverage >= 80 ? 'text-stone-800' : 'text-amber-700'}>{planQuality.coverage}%</strong></span>
              <span>共识指数 <strong className={planQuality.consensus >= 70 ? 'text-stone-800' : 'text-amber-700'}>{planQuality.consensus}</strong></span>
              {/* 保底路线必须一眼看出来：大模型不可用时方案是候选池合成的，
                  不能让它看起来像正常的智能体推演结果（后端同时会在核对面板里登记这条降级）。 */}
              {isFallbackRoute && (
                <span
                  className="text-amber-700"
                  data-testid="fallback-route-badge"
                  title="大模型暂时不可用时，方案改由高德候选池合成（真实 POI，但不是智能体推演结果）"
                >
                  保底路线 <strong>（非智能体推演）</strong>
                </span>
              )}
              {/* Make the learning step visible: which style the policy picked,
                  and whether it was exploring or exploiting. Without this the
                  bandit's decisions were invisible in the product. */}
              {banditDecision && (
                <span
                  className="text-stone-500"
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
              <span className="inline-flex items-center gap-1 text-stone-500"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />团队实时同步</span>
              {/* Probabilistic digital twin: show the sampled spread rather than a
                  single point estimate, so the user can see how much slack a day
                  actually has. */}
              {simulation?.total_minutes?.p50 ? (
                <span
                  className="text-stone-600"
                  title={`基于 ${simulation.assumptions?.sample_count ?? 0} 次蒙特卡洛采样；P90 表示 10% 的情形会超过该时长`}
                >
                  时长 P50 <strong>{Math.round((simulation.total_minutes.p50 ?? 0) / 60)}</strong>h
                  {' / '}P90 <strong>{Math.round((simulation.total_minutes.p90 ?? 0) / 60)}</strong>h
                </span>
              ) : null}
              {typeof simulation?.budget_overrun_probability === 'number' ? (
                <span
                  className={simulation.budget_overrun_probability > 0.3 ? 'text-rose-700' : 'text-slate-500'}
                  title="按采样得到的费用分布，超出预算的概率"
                >
                  超预算概率 <strong>{Math.round(simulation.budget_overrun_probability * 100)}%</strong>
                </span>
              ) : null}
              {simulation?.assumptions && (simulation.assumptions.unpriced_nodes ?? 0) > 0 ? (
                <span
                  className="text-amber-700"
                  title="这些节点缺少供应商报价，费用分布未包含它们"
                >
                  {simulation.assumptions.unpriced_nodes} 个节点未计价
                </span>
              ) : null}
              {planQuality.dailyCounts.length > 0 && <span className="text-slate-400">白天节点 {planQuality.dailyCounts.map((item: any) => `D${item.day}:${item.count}`).join(' · ')}</span>}
            </div>
          )}
          {phase === 'decision' && (
            <nav className="mt-4 flex items-center gap-1 rounded-xl border border-stone-200 bg-stone-50 p-1" aria-label="工作区层级">
              {([
                ['plan', '主方案', '路线、天数与预算'],
                ['evidence', '证据', '来源、时效与约束'],
                ['execution', '旅中执行', '状态、风险与重规划'],
              ] as const).map(([key, label, hint]) => (
                <button
                  key={key}
                  type="button"
                  data-testid={`workspace-layer-${key}`}
                  aria-pressed={workspaceLayer === key}
                  onClick={() => { setWorkspaceLayer(key); setShowWorkspaceMenu(false); }}
                  className={`min-h-10 flex-1 rounded-lg px-3 py-1.5 text-left transition-colors ${workspaceLayer === key ? 'bg-white text-stone-900 shadow-sm ring-1 ring-stone-200' : 'text-stone-500 hover:bg-white/70'}`}
                >
                  <span className="block text-[11px] font-black">{label}</span>
                  <span className="mt-0.5 hidden truncate text-[9px] font-medium text-stone-400 sm:block">{hint}</span>
                </button>
              ))}
              <div className="relative shrink-0">
                <button
                  type="button"
                  data-testid="workspace-layer-more"
                  aria-expanded={showWorkspaceMenu}
                  onClick={() => setShowWorkspaceMenu((open) => !open)}
                  className={`min-h-10 rounded-lg px-3 py-1.5 text-left transition-colors ${workspaceLayer === 'professional' ? 'bg-white text-stone-900 shadow-sm ring-1 ring-stone-200' : 'text-stone-500 hover:bg-white/70'}`}
                >
                  <span className="flex items-center gap-1 text-[11px] font-black"><MoreHorizontal className="h-3.5 w-3.5" />更多</span>
                  <span className="mt-0.5 hidden text-[9px] font-medium text-stone-400 sm:block">高级信息</span>
                </button>
                {showWorkspaceMenu && (
                  <div className="absolute right-0 top-full z-40 mt-2 w-52 rounded-xl border border-stone-200 bg-white p-1.5 shadow-xl">
                    <button
                      type="button"
                      data-testid="workspace-layer-professional"
                      onClick={() => { setWorkspaceLayer('professional'); setShowWorkspaceMenu(false); }}
                      className="w-full rounded-lg px-3 py-2 text-left hover:bg-stone-50"
                    >
                      <span className="block text-[11px] font-black text-stone-800">方案检查</span>
                      <span className="mt-0.5 block text-[10px] leading-4 text-stone-500">成员体验、策略依据与系统状态</span>
                    </button>
                  </div>
                )}
              </div>
            </nav>
          )}
        </header>

        <div className="flex-1 overflow-y-auto bg-white custom-scrollbar relative">
          {/* 核对说明是"要读的内容"，不是工具栏：放进滚动区最上面，跟行程正文一起滚。
              以前它挂在 header 里，而 header 是 auto 高度、不可滚动，所以面板一长
              （预算核对/已作调整/行程规模/本次调整/价格核对/跨城怎么走/长途分段/还需要你留意
              共 8 段）就把整列顶到 1200px 以上：正文区被压成 0 高、面板最后几段滚不到
              （1440×900 实测 header 1210px、正文 clientHeight 0、文档不可滚）。 */}
          {phase === 'decision' && workspaceLayer === 'evidence' && (
            <div className="px-5 pt-2 sm:px-8">
              <PlanGovernancePanel data={planGovernance} />
            </div>
          )}
          <AnimatePresence mode="wait">
            {phase === 'drafting' && (
              <>
                {/* 参考路线（可选）：**必须在规划之前** —— 它的用途是"用户没提要求时按它排"，
                    放到规划页就变成了"先排完再用"，顺序反了。默认折叠，不挤占主诉求输入。 */}
                <div className="px-4 pt-4 sm:px-8">
                  <ReferenceRoutePanel />
                </div>
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
              </>
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
                  {workspaceLayer === 'execution' && <>
                    <RiskPerturbationPanel alert={riskAlert} />
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
                  </>}
                  {workspaceLayer === 'plan' && <PreTripPlanningPanel
                      userId={currentUser.id}
                      tripId={planningResourceId}
                      destination={targetCityInfo?.name || initialDestination || ''}
                      baseNodes={currentDayRoutes}
                      budget={Number(budgetData?.total_budget || 0)}
                      days={totalDays.length}
                      travelers={Math.max(1, roomMembers.length)}
                    />}
                  {workspaceLayer === 'professional' && <section className="mb-4 rounded-2xl border border-stone-200 bg-[#fbfaf7] p-4 shadow-[0_8px_30px_rgba(67,45,25,0.06)]" data-testid="professional-diagnostics">
                    <div className="flex items-start justify-between gap-3">
                      <div><h3 className="text-sm font-black text-slate-800">方案检查</h3><p className="mt-1 text-[11px] text-slate-500">这里回答三个问题：路线是否可执行、谁可能不满意、下一步要不要调整。</p></div>
                      <button type="button" onClick={() => setShowBlackboard(true)} className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-[11px] font-bold text-stone-700 hover:bg-stone-50">查看取舍记录</button>
                    </div>
                    <div className="mt-3 grid gap-2 sm:grid-cols-3">
                      <div className="rounded-lg bg-white p-3"><p className="text-[10px] text-slate-400">最低成员体验</p><p className="mt-1 text-lg font-black text-slate-800">{fairnessAudit?.minimum_satisfaction != null ? `${Math.round(Number(fairnessAudit.minimum_satisfaction) * 100)}%` : '尚未评估'}</p><p className="mt-1 text-[10px] text-slate-400">越低的成员越需要优先照顾</p></div>
                      <div className="rounded-lg bg-white p-3"><p className="text-[10px] text-slate-400">路线是否均衡</p><p className={`mt-1 text-sm font-black ${fairnessAudit?.threshold_exceeded ? 'text-rose-600' : 'text-emerald-700'}`}>{fairnessAudit ? (fairnessAudit.threshold_exceeded ? '需要调整' : '当前可接受') : '尚未评估'}</p><p className="mt-1 text-[10px] text-slate-400">比较成员之间的体验差距</p></div>
                      <div className="rounded-lg bg-white p-3"><p className="text-[10px] text-slate-400">历史依据</p><p className="mt-1 text-sm font-black text-slate-800">{banditDecision ? (banditDecision.explored ? '正在试用新策略' : '优先使用稳定策略') : '样本不足'}</p><p className="mt-1 text-[10px] text-slate-400">样本不足时不会宣称“已经学会”</p></div>
                    </div>
                    <div className="mt-3 rounded-xl border border-[#e9d8c5] bg-[#fff8ef] px-3 py-2.5 text-[11px] leading-5 text-[#6f4527]">
                      <span className="font-black">你可以直接这样调整：</span> 换成更省钱、减少每天节点、优先某位成员，或打开路线节点查看来源与替代项。技术指标只放在下面的“高级诊断”里，不影响普通使用。
                    </div>
                    {/* 协商纪要 + 取价出处：属于"系统怎么想的"这类诊断信息，
                        所以放在专业诊断层（按需展开），而不是行程核对面板里。 */}
                    <NegotiationDiagnostics
                      negotiation={planGovernance?.negotiation as any}
                      priceAudit={planGovernance?.priceAudit as any}
                    />
                    {/* 统一事实记录（闭环第①段）：请求/约束/供应商/决策/结果/缺口，
                        每条都带来源与核实状态 —— "大家看的是同一份事实吗"要看这里。 */}
                    <PlanFactsPanel facts={planGovernance?.facts as any} />
                    <div className="mt-4 border-t border-slate-200 pt-3">
                      <button type="button" data-testid="toggle-advanced-diagnostics" aria-expanded={showAdvancedDiagnostics} onClick={() => setShowAdvancedDiagnostics((open) => !open)} className="flex w-full items-center justify-between gap-2 rounded-lg px-1 py-1 text-left transition-colors hover:bg-slate-100">
                        <span className="flex items-center gap-1.5 text-[11px] font-black text-slate-700">{showAdvancedDiagnostics ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}高级诊断</span>
                        <span className="text-[10px] text-slate-400">{agentDiagnosticsLoading ? '同步中' : `${agentCapabilities.length} 个专业模块`}</span>
                      </button>
                      {showAdvancedDiagnostics && <div className="mt-2">
                      {agentCapabilities.length > 0 ? <div className="grid gap-2 sm:grid-cols-2">
                        {agentCapabilities.map((capability: any) => {
                          const runtime = agentEvaluation[capability.agent_id] || agentEvaluation[capability.domain];
                          const events = Number(runtime?.events || 0);
                          const errors = Number(runtime?.errors || 0);
                          const status = events === 0 ? '无样本' : errors > 0 ? '有错误事件' : '有运行记录';
                          const statusClass = events === 0 ? 'text-amber-700 bg-amber-50' : errors > 0 ? 'text-rose-700 bg-rose-50' : 'text-emerald-700 bg-emerald-50';
                          return <article key={capability.agent_id} className="rounded-lg border border-slate-200 bg-white p-3" data-testid={`agent-capability-${capability.agent_id}`}>
                            <div className="flex items-start justify-between gap-2"><div><h4 className="text-xs font-black text-slate-800">{capability.label || capability.agent_id}</h4><p className="mt-1 text-[10px] leading-4 text-slate-500">{capability.responsibility}</p></div><span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold ${statusClass}`}>{status}</span></div>
                            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[9px] text-slate-400"><span>超时 {Math.round(Number(capability.timeout_ms || 0) / 1000)}s</span><span>事件 {events}</span><span>错误 {errors}</span></div>
                            <p className="mt-1 text-[9px] text-slate-400">降级：{capability.fallback || '未声明'} · 证据：{capability.evidence_required ? '必需' : '可选'}</p>
                          </article>;
                        })}
                      </div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] text-amber-700">能力矩阵暂不可用或尚无运行样本，当前不显示稳定性结论。</p>}
                      <button type="button" data-testid="toggle-agent-runtime-detail" aria-expanded={showAgentRuntimeDetail} onClick={() => setShowAgentRuntimeDetail((open) => !open)} className="mt-3 flex w-full items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-left transition-colors hover:bg-slate-50">
                        <span className="flex items-center gap-1.5 text-[10px] font-black text-slate-600">{showAgentRuntimeDetail ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}智能体运行明细</span>
                        <span className="text-[9px] text-slate-400">{agentContractAudits.length + (specialistRuntime ? 1 : 0)} 项</span>
                      </button>
                      {showAgentRuntimeDetail && <div>
                      {agentContractAudits.length > 0 && <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="agent-contract-audit"><p className="text-[10px] font-bold text-slate-500">本次方案提案审计</p><div className="mt-2 flex flex-wrap gap-2">{agentContractAudits.map((audit: any) => <span key={audit.agent_id || audit.domain} className={`rounded px-2 py-1 text-[10px] font-bold ${audit.status === 'ok' ? 'bg-emerald-50 text-emerald-700' : audit.status === 'degraded' ? 'bg-amber-50 text-amber-700' : 'bg-rose-50 text-rose-700'}`}>{audit.agent_id || '提案'} · {audit.status} · 证据 {audit.evidence_count ?? 0}</span>)}</div></div>}
                      {specialistRuntime && <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="specialist-runtime-summary">
                        <div className="flex items-center justify-between gap-2"><p className="text-[10px] font-bold text-slate-500">本次专业协作</p><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${specialistRuntime.status === 'ok' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{specialistRuntime.status === 'ok' ? '全部完成' : '部分降级'}</span></div>
                        <p className="mt-1.5 text-[11px] text-slate-600">交通、住宿、餐饮、风险和预算分别执行，共完成 {specialistRuntime.completed ?? 0} 项；降级 {specialistRuntime.degraded ?? 0} 项。</p>
                        <p className="mt-1 text-[10px] text-slate-400">专业提案先经过来源与格式校验，再进入路线约束和公平性裁决。</p>
                      </div>}
                      </div>}
                      {optimizationAudit && <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="pareto-audit-summary">
                        <div className="flex items-center justify-between gap-2"><p className="text-[10px] font-bold text-slate-500">候选裁决</p><span className="text-[9px] text-slate-400">{optimizationAudit.method || 'Pareto'}</span></div>
                        <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]"><div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">保留前沿 <strong className="ml-1 text-emerald-700">{optimizationAudit.frontier_count ?? 0}</strong></div><div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">被淘汰 <strong className="ml-1 text-slate-700">{optimizationAudit.rejected_count ?? 0}</strong></div></div>
                        {Array.isArray(optimizationAudit.rejected) && optimizationAudit.rejected.length > 0 && <div className="mt-2 space-y-1">{optimizationAudit.rejected.slice(0, 4).map((item: any, index: number) => <p key={index} className="text-[10px] text-slate-500">{item.candidate || '候选'}：{item.reason === 'dominated' ? `被 ${Array.isArray(item.dominated_by) ? item.dominated_by.join('、') : '更优候选'} 支配` : item.reason || '未通过门禁'}</p>)}</div>}
                      </div>}
                      {fairnessAudit?.repair_attempted && <p className={`mt-3 rounded-lg border px-3 py-2 text-[10px] ${fairnessAudit.threshold_exceeded ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>{fairnessAudit.threshold_exceeded ? '已尝试在候选方案中重新选路，但仍没有满足公平阈值的方案；请调整成员偏好或约束。' : '已通过公平性重选路。'}</p>}
                      <div className="mt-3 grid gap-3 lg:grid-cols-2">
                        <div className="rounded-lg border border-slate-200 bg-white p-3" data-testid="feature-snapshot-summary">
                          <div className="flex items-center justify-between"><p className="text-[10px] font-black text-slate-500">特征分层</p><span className="text-[9px] text-slate-400">{featureSnapshot?.feature_version || 'features-v1'}</span></div>
                          <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px]">
                            {['long_term', 'journey', 'realtime', 'behavior'].map((key) => <div key={key} className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">{key === 'long_term' ? '长期画像' : key === 'journey' ? '当前旅程' : key === 'realtime' ? '实时信号' : '行为统计'}<span className="ml-1 text-slate-400">{key === 'realtime' ? (featureSnapshot?.groups?.realtime?.status || '未持久化') : featureSnapshot?.groups?.[key] ? '可查看' : '待采集'}</span></div>)}
                          </div>
                          <p className="mt-2 text-[9px] leading-4 text-slate-400">原始反馈与提示词不直接暴露，实时天气、客流和延误仅接受供应商快照。</p>
                        </div>
                        <div className="rounded-lg border border-slate-200 bg-white p-3" data-testid="provider-registry-summary">
                          <div className="flex items-center justify-between"><p className="text-[10px] font-black text-slate-500">供应商字段注册</p><span className="text-[9px] text-slate-400">{providerRegistry.length} 个领域</span></div>
                          <div className="mt-2 flex flex-wrap gap-1.5">{providerRegistry.map((provider: any) => <span key={provider.kind} className={`rounded px-2 py-1 text-[9px] font-bold ${provider.configured ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>{provider.kind} · {provider.configured ? provider.provider : '未配置'}</span>)}</div>
                          <p className="mt-2 text-[9px] leading-4 text-slate-400">字段必须带来源、observed_at 和估算标记，注册表不返回密钥或原始响应。</p>
                          <div className="mt-3 border-t border-slate-100 pt-3" data-testid="provider-quality-summary">
                            <div className="flex items-center justify-between gap-2"><p className="text-[10px] font-black text-slate-500">字段质量快照</p><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${providerQuality?.status === 'available' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{providerQuality?.status === 'available' ? `近 ${providerQuality?.window_days || 7} 天` : '暂无探测样本'}</span></div>
                            {Array.isArray(providerQuality?.providers) && providerQuality.providers.length > 0 ? <div className="mt-2 space-y-1.5">{providerQuality.providers.slice(0, 6).map((item: any) => {
                              const coverage = item.coverage || {};
                              const factCoverage = Number(coverage.fact_usable || 0);
                              const sourceCoverage = Number(coverage.source || 0);
                              const decisionGate = item.decision_gate || {};
                              const decisionGateAvailable = decisionGate.status === 'available';
                              const eligibleCount = Number(decisionGate.eligible_count || 0);
                              const blockedCount = Number(decisionGate.blocked_count || 0);
                              const enumInvalidRatio = Number(item.enum_invalid_ratio || 0);
                              const freshness = Number(item.stale_ratio || 0) > 0 ? '有过期' : Number(item.estimated_ratio || 0) > 0 ? '含估算' : '新鲜度正常';
                              return <div key={`${item.kind}-${item.provider}`} className="rounded bg-slate-50 px-2 py-1.5 text-[9px] text-slate-600"><div className="flex items-center justify-between gap-2"><span className="font-bold text-slate-700">{item.kind} · {item.provider}</span><span className={item.last_status === 'ok' ? 'text-emerald-700' : 'text-amber-700'}>{item.last_status === 'ok' ? '最近成功' : '最近无返回'}</span></div><div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-slate-500"><span>候选 {item.candidate_count ?? 0}</span><span>来源 {Math.round(sourceCoverage * 100)}%</span><span>可用事实 {Math.round(factCoverage * 100)}%</span><span>实时决策 {decisionGateAvailable ? `${eligibleCount}/${eligibleCount + blockedCount || item.candidate_count || 0}` : '未采集'}</span><span className={enumInvalidRatio > 0 ? 'font-bold text-amber-700' : ''}>枚举异常 {Math.round(enumInvalidRatio * 100)}%</span><span>{freshness}</span>{item.mean_latency_ms != null && <span>延迟 {Math.round(Number(item.mean_latency_ms))}ms</span>}</div>{decisionGateAvailable && blockedCount > 0 && <p className="mt-1 text-amber-700">{blockedCount} 个候选缺少新鲜、带观测时间的关键字段，仅保留官方核验入口。</p>}{enumInvalidRatio > 0 && <p className="mt-1 text-amber-700">部分天气、客流或可用状态无法识别，未参与实时事实决策。</p>}{item.last_error_code && <p className="mt-1 text-rose-600">状态码：{item.last_error_code}</p>}</div>;
                            })}</div> : <p className="mt-2 rounded bg-amber-50 px-2 py-1.5 text-[9px] text-amber-700">尚未产生供应商质量事件；配置供应商并完成一次查询后，这里会显示字段覆盖率和新鲜度。</p>}
                            <p className="mt-2 text-[9px] leading-4 text-slate-400">只保存聚合计数，不保存供应商原始响应、URL 查询参数或凭据。</p>
                            {Array.isArray(providerQualityTrend?.points) && providerQualityTrend.points.length > 0 && <div className="mt-3 border-t border-slate-100 pt-3" data-testid="provider-quality-trend">
                              <div className="flex items-center justify-between gap-2"><p className="text-[10px] font-black text-slate-500">来源质量趋势</p><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${providerQualityTrend.status === 'available' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{providerQualityTrend.status === 'available' ? `近 ${providerQualityTrend.window_days || 14} 天` : '样本不足'}</span></div>
                              <p className="mt-1 text-[9px] leading-4 text-slate-400">按日聚合可用事实与多来源冲突；不展示用户、行程或供应商原始响应。</p>
                              <div className="mt-2 space-y-1.5">{providerQualityTrend.points.slice(-7).map((point: any, index: number) => {
                                const eligible = Math.round(Number(point.eligible_ratio || 0) * 100);
                                const conflicts = Math.round(Number(point.source_conflict_ratio || 0) * 100);
                                const enumInvalid = Math.round(Number(point.enum_invalid_ratio || 0) * 100);
                                return <div key={`${point.date}-${point.kind}-${point.provider}-${index}`} className="rounded bg-slate-50 px-2 py-1.5 text-[9px] text-slate-600">
                                  <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1"><span className="font-bold text-slate-700">{point.date} · {point.kind} · {point.provider}</span><span>样本 {point.candidate_count ?? 0}</span></div>
                                  <div className="mt-1 grid grid-cols-[1fr_auto] items-center gap-2"><div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(100, Math.max(0, eligible))}%` }} /></div><span className="text-emerald-700">可用 {eligible}%</span></div>
                                  <div className="mt-1 grid grid-cols-[1fr_auto] items-center gap-2"><div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-amber-400" style={{ width: `${Math.min(100, Math.max(0, conflicts))}%` }} /></div><span className={conflicts > 0 ? 'text-amber-700' : 'text-slate-400'}>冲突 {conflicts}%</span></div>
                                  <div className="mt-1 grid grid-cols-[1fr_auto] items-center gap-2"><div className="h-1.5 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-rose-400" style={{ width: `${Math.min(100, Math.max(0, enumInvalid))}%` }} /></div><span className={enumInvalid > 0 ? 'text-rose-700' : 'text-slate-400'}>枚举异常 {enumInvalid}%</span></div>
                                </div>;
                              })}</div>
                            </div>}
                          </div>
                        </div>
                      </div>
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="quality-evaluation-summary">
                        <div className="flex items-center justify-between"><p className="text-[10px] font-black text-slate-500">持续评估</p><span className={`rounded px-2 py-1 text-[9px] font-bold ${qualitySummary?.training_ready ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{qualitySummary?.training_ready ? '可进入离线复核' : '样本不足'}</span></div>
                        <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] sm:grid-cols-4">
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">事件<span className="ml-1 font-black text-slate-800">{qualitySummary?.events ?? 0}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">采纳率<span className="ml-1 font-black text-slate-800">{qualitySummary?.rates?.adoption != null ? `${Math.round(Number(qualitySummary.rates.adoption) * 100)}%` : '—'}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">漂移<span className="ml-1 font-black text-slate-800">{qualitySummary?.drift?.status || '无样本'}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">反事实<span className="ml-1 font-black text-slate-800">{qualitySummary?.counterfactual?.status || '待采集'}</span></div>
                        </div>
                        {qualitySummary?.provider_quality_gate && (() => {
                          const gate = qualitySummary.provider_quality_gate;
                          const status = String(gate.status || 'not_required');
                          const isBlocked = status === 'blocked';
                          const isReady = status === 'available';
                          const style = isBlocked ? 'border-rose-200 bg-rose-50 text-rose-700' : isReady ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700';
                          const label = isBlocked ? '供应商质量阻断发布' : isReady ? '供应商质量门禁通过' : status === 'insufficient_data' ? '供应商质量样本不足' : '尚无供应商质量样本';
                          return <div className={`mt-2 rounded-lg border px-2.5 py-2 text-[9px] ${style}`} data-testid="provider-quality-release-gate"><div className="flex flex-wrap items-center justify-between gap-2"><span className="font-black">{label}</span><span>通过 {gate.ready_providers ?? 0} · 不足 {gate.insufficient_providers ?? 0} · 阻断 {gate.blocked_providers ?? 0}</span></div>{Array.isArray(gate.alerts) && gate.alerts.length > 0 && <div className="mt-1 space-y-0.5" data-testid="provider-quality-alerts">{gate.alerts.slice(0, 4).map((alert: any) => <p key={`${alert.code}-${alert.severity}`} className={alert.severity === 'critical' ? 'font-bold text-rose-700' : 'font-medium text-amber-700'}>{alert.severity === 'critical' ? '需处理' : '需关注'}：{alert.code}（{alert.affected_providers ?? 0} 个供应商）</p>)}</div>}{Array.isArray(gate.reasons) && gate.reasons.length > 0 && <p className="mt-1 leading-4">异常：{gate.reasons.slice(0, 3).join('；')}</p>}{status === 'insufficient_data' && <p className="mt-1 leading-4">证据不足时不会将供应商或模型标记为优质。</p>}</div>;
                        })()}
                        <p className="mt-2 text-[9px] leading-4 text-slate-400">质量指标只接受注册指标和带数值的智能体事件；样本不足时不会显示“模型已学会”。</p>
                        {qualitySummary?.quality_metrics?.replan_success_rate_by_trigger && <div className="mt-2 space-y-1.5">{Object.entries(qualitySummary.quality_metrics.replan_success_rate_by_trigger as Record<string, any>).map(([trigger, value]) => <div key={trigger} className="rounded bg-slate-50 px-2 py-1.5 text-[9px] font-bold text-slate-600"><div className="flex flex-wrap justify-between gap-2"><span>{trigger}</span><span className={value.status === 'available' ? 'text-slate-700' : 'text-amber-700'}>{value.status === 'available' ? `${Math.round(Number(value.success_rate || 0) * 100)}%` : '样本不足'} · 成功 {value.successful ?? 0} · 拒绝 {value.rejected ?? 0} · 失败 {value.failed ?? 0}</span></div>{Object.keys(value.failure_code_distribution || {}).length > 0 && <p className="mt-1 text-[8px] font-medium text-rose-600">失败码：{Object.entries(value.failure_code_distribution as Record<string, number>).map(([code, count]) => `${code} × ${count}`).join('、')}</p>}{value.models && Object.keys(value.models).length > 0 && <p className="mt-1 text-[8px] font-medium text-slate-400">版本：{Object.entries(value.models as Record<string, any>).map(([version, model]) => `${version} ${model.status === 'available' ? `${Math.round(Number(model.success_rate || 0) * 100)}%` : '样本不足'}`).join(' · ')}</p>}</div>)}</div>}
                      </div>
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="source-verification-funnel">
                        <div className="flex items-center justify-between gap-2"><div><p className="text-[10px] font-black text-slate-500">官方核验闭环</p><p className="mt-1 text-[9px] text-slate-400">从跳转到核验、采用的真实行为漏斗</p></div><span className={`rounded px-2 py-1 text-[9px] font-bold ${channelMetrics?.status === 'attribution_ready' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{channelMetrics?.status === 'attribution_ready' ? '可归因' : '待采集'}</span></div>
                        <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] sm:grid-cols-4">
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">跳转<span className="ml-1 font-black text-slate-800">{channelMetrics?.funnel?.clicks ?? 0}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">返回<span className="ml-1 font-black text-slate-800">{channelMetrics?.funnel?.returned ?? 0}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">核验<span className="ml-1 font-black text-slate-800">{channelMetrics?.funnel?.verified ?? 0}</span></div>
                          <div className="rounded bg-slate-50 px-2 py-1.5 text-slate-600">采用<span className="ml-1 font-black text-slate-800">{channelMetrics?.funnel?.adopted ?? 0}</span></div>
                        </div>
                        <p className="mt-2 text-[9px] leading-4 text-slate-400">返回率 {Math.round(Number(channelMetrics?.rates?.return_rate || 0) * 100)}% · 核验率 {Math.round(Number(channelMetrics?.rates?.verification_rate || 0) * 100)}% · 采用率 {Math.round(Number(channelMetrics?.rates?.adoption_rate || 0) * 100)}%</p>
                        <p className="mt-1 text-[9px] leading-4 text-slate-400">平台不把跳转视为已预订；“采用”仅代表用户明确确认候选。</p>
                      </div>
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="model-runtime-health">
                        <div className="flex items-center justify-between gap-2">
                          <div><p className="text-[10px] font-black text-slate-500">线上模型健康</p><p className="mt-1 text-[9px] text-slate-400">最近 {runtimeHealth?.window_hours ?? 24} 小时 · 仅展示聚合指标</p></div>
                          <span className="rounded px-2 py-1 text-[9px] font-bold text-slate-500 bg-slate-50">自动回滚已启用</span>
                        </div>
                        {runtimeModels.length > 0 ? <div className="mt-2 space-y-2">
                          {runtimeModels.map((model: any) => {
                            const status = String(model.status || 'insufficient_data');
                            const deploymentStatus = String(model.deployment_status || 'unknown');
                            const statusClass = status === 'healthy' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : status === 'rollback_required' ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-amber-200 bg-amber-50 text-amber-700';
                            const rate = model.adoption_rate == null ? '—' : `${Math.round(Number(model.adoption_rate) * 100)}%`;
                            return <article key={model.version} className="rounded-lg border border-slate-200 p-2.5" data-testid={`model-runtime-${model.version}`}>
                              <div className="flex items-start justify-between gap-2"><div><p className="text-[11px] font-black text-slate-800">{model.version || '未命名版本'}</p><p className="mt-0.5 text-[9px] text-slate-400">部署：{deploymentStatus === 'canary' ? '灰度' : deploymentStatus === 'active' ? '主版本' : deploymentStatus === 'rolled_back' ? '已回滚' : deploymentStatus}</p></div><span className={`rounded border px-1.5 py-0.5 text-[9px] font-bold ${statusClass}`}>{runtimeStatusLabel(status)}</span></div>
                              <div className="mt-2 grid grid-cols-2 gap-1.5 text-[9px] text-slate-500 sm:grid-cols-4"><span className="rounded bg-slate-50 px-2 py-1">样本 <strong className="text-slate-800">{model.samples ?? 0}</strong></span><span className="rounded bg-slate-50 px-2 py-1">采纳 <strong className="text-slate-800">{rate}</strong></span><span className="rounded bg-slate-50 px-2 py-1">错误 <strong className="text-slate-800">{model.error_rate == null ? '—' : `${Math.round(Number(model.error_rate) * 100)}%`}</strong></span><span className="rounded bg-slate-50 px-2 py-1">公平方差 <strong className="text-slate-800">{model.fairness_utility_variance == null ? '—' : Number(model.fairness_utility_variance).toFixed(3)}</strong></span></div>
                              {Array.isArray(model.reasons) && model.reasons.length > 0 && <p className="mt-1.5 text-[9px] leading-4 text-rose-600">回滚原因：{model.reasons.join('、')}</p>}
                              {status === 'insufficient_data' && <p className="mt-1.5 text-[9px] leading-4 text-amber-700">样本不足，不对该版本做优劣判断。</p>}
                            </article>;
                          })}
                        </div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[10px] leading-4 text-amber-700">暂无模型运行样本；灰度状态将在产生带版本事件后显示。</p>}
                      </div>
                      <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="evaluation-audit-summary">
                        <div className="flex items-center justify-between gap-2"><div><p className="text-[10px] font-black text-slate-500">评估任务</p><p className="mt-1 text-[9px] text-slate-400">周期任务最近一次执行状态</p></div><span className={`rounded px-2 py-1 text-[9px] font-bold ${evaluationAudit?.health?.status === 'healthy' ? 'bg-emerald-50 text-emerald-700' : evaluationAudit?.health?.status === 'critical' ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-700'}`}>{evaluationAudit?.health?.status === 'healthy' ? '任务正常' : evaluationAudit?.health?.status === 'critical' ? '需要处理' : evaluationAudit?.health?.status === 'warning' ? '需要关注' : '尚无记录'}</span></div>
                        {evaluationAudit?.health && <p className={`mt-2 rounded-lg border px-2.5 py-2 text-[9px] leading-4 ${evaluationAudit.health.status === 'critical' ? 'border-rose-200 bg-rose-50 text-rose-700' : evaluationAudit.health.status === 'warning' ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-slate-200 bg-slate-50 text-slate-600'}`}>{evaluationAudit.health.reason} · 连续失败 {evaluationAudit.health.consecutive_failures ?? 0} 次{evaluationAudit.health.age_seconds != null ? ` · 距最近执行 ${Math.round(Number(evaluationAudit.health.age_seconds) / 60)} 分钟` : ''}</p>}
                        {evaluationAudit?.latest ? <div className="mt-2 rounded-lg bg-slate-50 p-2.5"><div className="flex items-center justify-between gap-2"><span className="text-[10px] font-black text-slate-800">{evaluationAudit.latest.model_version || '未标注版本'}</span><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${evaluationAudit.latest.status === 'ready' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>{evaluationAudit.latest.status === 'ready' ? '可进入门禁' : '证据不足/已阻断'}</span></div><div className="mt-1.5 grid grid-cols-2 gap-1.5 text-[9px] text-slate-500"><span>样本 <strong className="text-slate-800">{evaluationAudit.latest.sample_count ?? 0}</strong></span><span>验证集 <strong className="text-slate-800">{evaluationAudit.latest.validation_samples ?? 0}</strong></span><span>回写 <strong className="text-slate-800">{evaluationAudit.latest.callback?.accepted === true ? '成功' : evaluationAudit.latest.callback?.skipped ? '试运行' : '未成功'}</strong></span><span>时间 <strong className="text-slate-800">{evaluationAudit.latest.recorded_at ? new Date(Number(evaluationAudit.latest.recorded_at) * 1000).toLocaleString() : '—'}</strong></span></div>{evaluationAudit.latest.provider_quality_status && <div className={`mt-2 rounded-lg border px-2.5 py-2 text-[9px] ${evaluationAudit.latest.provider_quality_status === 'available' || evaluationAudit.latest.provider_quality_status === 'not_required' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : evaluationAudit.latest.provider_quality_status === 'blocked' ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`} data-testid="evaluation-provider-quality"><div className="flex items-center justify-between gap-2"><span className="font-bold">本次评估的供应商质量门禁</span><span className="font-black">{evaluationAudit.latest.provider_quality_status === 'available' ? '通过' : evaluationAudit.latest.provider_quality_status === 'not_required' ? '未启用' : evaluationAudit.latest.provider_quality_status === 'blocked' ? '阻断' : '样本不足'}</span></div><p className="mt-1">覆盖 {evaluationAudit.latest.provider_quality_ready_providers ?? 0}/{evaluationAudit.latest.provider_quality_providers ?? 0} 个供应商 · 不足 {evaluationAudit.latest.provider_quality_insufficient_providers ?? 0} · 阻断 {evaluationAudit.latest.provider_quality_blocked_providers ?? 0}</p></div>}{evaluationAudit.latest.policy_comparison_status && <div className="mt-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[9px] text-slate-600"><div className="flex items-center justify-between gap-2"><span className="font-bold text-slate-700">行为排序对照</span><span className={evaluationAudit.latest.policy_comparison_status === 'available' && evaluationAudit.latest.policy_comparison_ready === true ? 'font-bold text-emerald-700' : 'font-bold text-amber-700'}>{evaluationAudit.latest.policy_comparison_status === 'available' && evaluationAudit.latest.policy_comparison_ready === true ? '证据可用' : '样本不足/未通过'}</span></div><p className="mt-1 leading-4">基线 {evaluationAudit.latest.policy_baseline_samples ?? 0} 条 · 行为 {evaluationAudit.latest.policy_behavior_samples ?? 0} 条 · reward 提升 {evaluationAudit.latest.policy_reward_lift == null ? '—' : `${(Number(evaluationAudit.latest.policy_reward_lift) * 100).toFixed(1)}%`}</p></div>}{evaluationAudit.latest.fairness_segment_status && <div className="mt-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[9px] text-slate-600" data-testid="segment-fairness-audit"><div className="flex items-center justify-between gap-2"><span className="font-bold text-slate-700">分层公平门禁</span><span className={evaluationAudit.latest.fairness_segment_status === 'available' && Number(evaluationAudit.latest.fairness_segment_threshold_exceeded || 0) === 0 && Number(evaluationAudit.latest.fairness_segment_max_stddev ?? 1) <= 0.1 ? 'font-bold text-emerald-700' : 'font-bold text-amber-700'}>{evaluationAudit.latest.fairness_segment_status === 'available' ? '已覆盖' : '样本不足'}</span></div><p className="mt-1 leading-4">最大标准差 {evaluationAudit.latest.fairness_segment_max_stddev == null ? '—' : Number(evaluationAudit.latest.fairness_segment_max_stddev).toFixed(3)} / 0.100 · 超阈值样本 {evaluationAudit.latest.fairness_segment_threshold_exceeded ?? '—'}</p><p className="mt-1 text-[8px] text-slate-400">按人群、目的地、季节和预算分层独立统计，不借用其他分层样本。</p>{evaluationAudit.latest.fairness_segment_dimensions && <div className="mt-2 grid grid-cols-2 gap-1.5" data-testid="segment-fairness-dimensions">{Object.entries(evaluationAudit.latest.fairness_segment_dimensions as Record<string, any>).map(([dimension, summary]: [string, any]) => <div key={dimension} className="rounded bg-slate-50 px-2 py-1.5"><div className="flex items-center justify-between gap-1"><span className="font-bold text-slate-600">{dimension === 'traveler_group' ? '人群' : dimension === 'budget_bucket' ? '预算' : dimension === 'destination' ? '目的地' : '季节'}</span><span className={summary?.status === 'available' ? 'font-bold text-emerald-700' : 'font-bold text-amber-700'}>{summary?.status === 'available' ? '可用' : '不足'}</span></div><p className="mt-1 text-[8px] text-slate-400">覆盖 {summary?.available_segments ?? 0}/{summary?.segment_count ?? 0} · 标准差 {summary?.max_stddev == null ? '—' : Number(summary.max_stddev).toFixed(3)}</p></div>)}</div>}</div>}{Array.isArray(evaluationAudit.latest.reason) && evaluationAudit.latest.reason.length > 0 && <p className="mt-1.5 text-[9px] leading-4 text-amber-700">阻断原因：{evaluationAudit.latest.reason.join('、')}</p>}</div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[10px] leading-4 text-amber-700">评估任务尚未产生审计记录；这不代表模型已通过评估。</p>}
                      </div>
                      {Array.isArray(evaluationAudit?.timeline) && <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="evaluation-timeline">
                        <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-[10px] font-black text-slate-500">模型评估时间线</p><p className="mt-1 text-[9px] text-slate-400">评估、影子运行、状态变化和回滚的脱敏摘要</p></div><div className="flex items-center gap-2"><span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${auditCleanupHealth?.status === 'healthy' ? 'bg-emerald-50 text-emerald-700' : auditCleanupHealth?.status === 'warning' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-500'}`}>清理 {auditCleanupHealth?.status === 'healthy' ? '正常' : auditCleanupHealth?.status === 'warning' ? '需关注' : '待执行'}</span><span className="text-[9px] text-slate-400">最近 {evaluationAudit.timeline.length} 条 · {evaluationAudit.timeline_source === 'persisted' ? '持久化审计' : evaluationAudit.timeline_source === 'derived' ? '兼容观察' : '持久化 + 兼容观察'}</span></div></div>
                        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-slate-100 bg-slate-50 p-2" data-testid="evaluation-timeline-filters">
                          <select aria-label="模型版本筛选" value={auditVersionFilter} onChange={(event) => setAuditVersionFilter(event.target.value)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-bold text-slate-600 outline-none focus:border-orange-400">
                            <option value="">全部版本</option>
                            {Array.from(new Set((evaluationAudit.timeline as any[]).map((item: any) => String(item.version || '')).filter(Boolean))).sort().map((version) => <option key={version} value={version}>{version}</option>)}
                          </select>
                          <label className="flex items-center gap-1 text-[10px] font-bold text-slate-500">起始日期<input aria-label="审计起始日期" type="date" value={auditSinceFilter} onChange={(event) => setAuditSinceFilter(event.target.value)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-medium text-slate-600 outline-none focus:border-orange-400" /></label>
                          {(auditVersionFilter || auditSinceFilter) && <button type="button" onClick={() => { setAuditVersionFilter(''); setAuditSinceFilter(''); }} className="rounded-md px-2 py-1 text-[10px] font-bold text-slate-500 hover:bg-white hover:text-slate-800">清除筛选</button>}
                          {evaluationAudit.retention_days && <span className="ml-auto text-[9px] text-slate-400">保留 {evaluationAudit.retention_days} 天{auditCleanupHealth?.last_deleted != null ? ` · 最近清理 ${auditCleanupHealth.last_deleted} 条` : ''}</span>}
                        </div>
                        {auditSummary?.status === 'available' && <div className="mt-2 grid grid-cols-2 gap-1.5 text-[9px] text-slate-500 sm:grid-cols-4"><span className="rounded bg-slate-50 px-2 py-1">事件 <strong className="text-slate-800">{auditSummary.count ?? 0}</strong></span><span className="rounded bg-slate-50 px-2 py-1">样本 <strong className="text-slate-800">{auditSummary.total_samples ?? 0}</strong></span><span className="rounded bg-slate-50 px-2 py-1">阻断 <strong className="text-slate-800">{auditSummary.statuses?.insufficient_evidence ?? 0}</strong></span><span className="rounded bg-slate-50 px-2 py-1">回滚 <strong className="text-slate-800">{auditSummary.statuses?.rolled_back ?? 0}</strong></span></div>}
                        {auditSummary?.status === 'available' && Object.keys(auditSummary.reason_codes || {}).length > 0 && <p className="mt-1.5 text-[9px] leading-4 text-slate-400">高频原因：{Object.entries(auditSummary.reason_codes as Record<string, number>).sort(([, left], [, right]) => Number(right) - Number(left)).slice(0, 3).map(([code, count]) => `${code} × ${count}`).join('、')}</p>}
                        {auditComparison?.status === 'available' && Array.isArray(auditComparison.versions) && auditComparison.versions.length > 0 && <div className="mt-2 overflow-x-auto rounded-lg border border-slate-100"><table className="w-full min-w-[420px] text-left text-[9px] text-slate-500"><thead className="bg-slate-50 text-[8px] font-black uppercase tracking-wide text-slate-400"><tr><th className="px-2 py-1.5">版本</th><th className="px-2 py-1.5">样本</th><th className="px-2 py-1.5">阻断率</th><th className="px-2 py-1.5">回滚</th><th className="px-2 py-1.5">证据</th></tr></thead><tbody>{auditComparison.versions.map((entry: any) => <tr key={entry.version} className="border-t border-slate-100"><td className="px-2 py-1.5 font-bold text-slate-700">{entry.version}</td><td className="px-2 py-1.5">{entry.samples ?? 0}</td><td className="px-2 py-1.5">{Math.round(Number(entry.block_rate || 0) * 100)}%</td><td className="px-2 py-1.5">{entry.rollbacks ?? 0}</td><td className={`px-2 py-1.5 font-bold ${entry.sufficient_evidence ? 'text-emerald-700' : 'text-amber-700'}`}>{entry.sufficient_evidence ? '足够' : '不足'}</td></tr>)}</tbody></table><p className="px-2 py-1.5 text-[8px] text-slate-400">仅比较聚合统计；样本不足时不判定策略优劣。</p></div>}
                        {evaluationAudit.timeline.length > 0 ? <div className="mt-2 space-y-1.5">{evaluationAudit.timeline.map((item: any, index: number) => <div key={`${item.id || item.timestamp || index}-${item.kind || 'event'}`} className="flex items-start gap-2 rounded-lg bg-slate-50 px-2.5 py-2"><span className={`mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full ${item.kind === 'rollback' ? 'bg-rose-500' : item.kind === 'evaluation' ? 'bg-amber-500' : item.kind === 'shadow' ? 'bg-orange-400' : 'bg-slate-400'}`} /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-x-2 gap-y-0.5"><span className="text-[10px] font-bold text-slate-700">{item.summary || item.kind}</span><span className="text-[9px] text-slate-400">{item.version || '未标注版本'}</span></div><p className="mt-0.5 text-[9px] text-slate-400">{item.at ? new Date(item.at).toLocaleString() : '—'}{item.sample_count != null ? ` · 样本 ${item.sample_count}` : ''}</p>{Array.isArray(item.reason_codes) && item.reason_codes.length > 0 && <p className="mt-1 text-[9px] leading-4 text-amber-700">原因码：{item.reason_codes.join('、')}</p>}</div></div>)}</div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[10px] leading-4 text-amber-700">当前筛选范围内暂无审计事件；样本不足不代表模型已通过评估。</p>}
                        {evaluationAudit?.pagination?.has_more && <button type="button" onClick={() => void loadEarlierAuditEvents()} disabled={auditLoadingMore} className="mt-2 w-full rounded-md border border-slate-200 bg-white px-2.5 py-2 text-[10px] font-bold text-slate-600 transition hover:border-orange-300 hover:text-orange-700 disabled:cursor-wait disabled:opacity-60">{auditLoadingMore ? '正在加载更早事件…' : '加载更早事件'}</button>}
                      </div>}
                      </div>}
                    </div>
                  </section>}
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
                    {planSwitchNotice && <span role="status" className="basis-full text-[11px] font-bold text-emerald-700">{planSwitchNotice}</span>}
                  </div>
                )}
                {workspaceLayer === 'plan' && <MafengwoStylePanel
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
                  fairnessAudit={fairnessAudit}
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
                 />}
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
          allRoutes={dynamicRoutes}
          activeDay={totalDays[activeDayIndex]}
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
          planVariants={planVariants}
          activePlanId={activePlanId}
          optimizationAudit={optimizationAudit}
          onSelectPlanVariant={handleSelectPlan}
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
                const data = replanPayload?.data || replanPayload;
                const candidateCount = Array.isArray(data?.replacement_candidates) ? data.replacement_candidates.length : 0;
                setReplanProposal({
                  proposalId: proposal?.proposal_id || payload?.data?.proposal_id || payload?.proposal_id || '',
                  action: { ...action, trigger_types: proposal?.trigger_types || [], expires_at: proposal?.expires_at },
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
            title="查看方案取舍与协作记录"
          >
            <BrainCircuit className="w-4 h-4 text-orange-500"/>
            <span className="hidden sm:inline">方案取舍</span>
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
                <div>
                  <h3 className="font-bold text-slate-800 flex items-center gap-2 text-sm"><BrainCircuit className="w-4 h-4 text-orange-500"/> 方案取舍记录</h3>
                  <p className="mt-1 text-[10px] text-slate-500">记录系统如何在预算、距离、成员偏好和风险之间做选择；它不是聊天记录，也不会改变已确认的路线。</p>
                </div>
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
                  <h3 className="font-bold text-slate-800 flex items-center gap-2 text-sm"><Share2 className="w-4 h-4 text-orange-600"/> 行程海报</h3>
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
                        {city ? `${city} · ` : ''}{photoModalPoi.type || '景点'} · 搜索词：<span className="text-orange-600 font-bold">{preciseQuery}</span>
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
                    当前搜索词：<span className="font-bold text-orange-600">{preciseQuery}</span> — 已包含城市名以精确定位
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

// ============== 同行网络协同沙盘 · 实时动态版 ==============

