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
  Activity, Radio, Wifi, Zap, Signal, Share2, Link2, Download, Train
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { API_BASE, WS_BASE, apiFetch, getAuthToken, installApiFetchInterceptor } from './lib/utils';
import { shortenRegionName, tryExtractJson, normalizeLnglat, extractStreamingRoutes } from './lib/lobbyUtils';
import type { CommunityComment, Phase, ProfileTrip, RoomMember, UserProfile } from './types';

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
                  // 概率化仿真的分布结果（P50/P90 与超预算概率）
                  if (finalData.simulation && typeof finalData.simulation === 'object') {
                    setSimulation(finalData.simulation);
                  } else {
                    setSimulation(null);
                  }
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
              {/* Probabilistic digital twin: show the sampled spread rather than a
                  single point estimate, so the user can see how much slack a day
                  actually has. */}
              {simulation?.total_minutes?.p50 ? (
                <span
                  className="text-indigo-600"
                  title={`基于 ${simulation.assumptions?.sample_count ?? 0} 次蒙特卡洛采样；P90 表示 10% 的情形会超过该时长`}
                >
                  时长 P50 <strong>{Math.round((simulation.total_minutes.p50 ?? 0) / 60)}</strong>h
                  {' / '}P90 <strong>{Math.round((simulation.total_minutes.p90 ?? 0) / 60)}</strong>h
                </span>
              ) : null}
              {typeof simulation?.budget_overrun_probability === 'number' ? (
                <span
                  className={simulation.budget_overrun_probability > 0.3 ? 'text-rose-600' : 'text-slate-500'}
                  title="按采样得到的费用分布，超出预算的概率"
                >
                  超预算概率 <strong>{Math.round(simulation.budget_overrun_probability * 100)}%</strong>
                </span>
              ) : null}
              {simulation?.assumptions && (simulation.assumptions.unpriced_nodes ?? 0) > 0 ? (
                <span
                  className="text-amber-600"
                  title="这些节点缺少供应商报价，费用分布未包含它们"
                >
                  {simulation.assumptions.unpriced_nodes} 个节点未计价
                </span>
              ) : null}
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

// ============== 同行网络协同沙盘 · 实时动态版 ==============

