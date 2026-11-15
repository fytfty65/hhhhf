'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { generatedAvatarUrl } from '../lib/avatarArt';
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  Camera,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  Download,
  Edit3,
  FileDown,
  Filter,
  MapPin,
  MessageCircle,
  Play,
  RefreshCw,
  Search,
  Share2,
  Compass,
  ShieldCheck,
  X,
} from 'lucide-react';
import { apiJson, assetUrl } from '../lib/apiClient';
import { buildDiaryMarkdown } from '../lib/tripExport';
import { useFocusTrap } from '../lib/useFocusTrap';
import type { CommunityPostItem, ProfileTrip, UserProfile } from '../types';
import AvatarUploader from './AvatarUploader';
import AccountSecurity from './AccountSecurity';
import GuestAccountPanel from './GuestAccountPanel';
import UserTraitGraphCard from './UserTraitGraphCard';

type TripFilter = 'all' | 'recent' | 'shared';
type TripData = { trips?: ProfileTrip[] };
type PostsData = { posts?: CommunityPostItem[] };
type LearningQuality = { training_ready?: boolean; events?: number; learning_readiness?: { reason?: string; total_samples?: number; minimum_total_samples?: number } };

// Shared portal surface. Keeping the class in one place prevents the profile
// from drifting into a collection of unrelated cards as new modules land.
const panel = 'profile-panel border border-line bg-paper-card';

function textValue(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : value == null ? fallback : String(value);
}

function parseTripContent(content: string) {
  try {
    const value = JSON.parse(content);
    return value && typeof value === 'object' ? value as { summary?: string; city?: string; days?: unknown[]; routes?: any[]; budget?: number } : null;
  } catch {
    return null;
  }
}

function tripTitle(trip: ProfileTrip, parsed: ReturnType<typeof parseTripContent>) {
  return textValue(trip.Title) || (parsed?.city ? `${parsed.city} 行程` : '未命名路书');
}

function tripCity(trip: ProfileTrip, parsed: ReturnType<typeof parseTripContent>) {
  return textValue(trip.DestCity) || textValue(parsed?.city);
}

function tripDate(value: string) {
  if (!value) return '尚未记录日期';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function routeDays(parsed: ReturnType<typeof parseTripContent>) {
  if (!parsed) return 0;
  const nodes = routeNodes(parsed);
  const declaredDays = Array.isArray(parsed.days) ? parsed.days.length : 0;
  const values = nodes.map((node) => Number(node?.day)).filter((day) => Number.isFinite(day) && day > 0);
  return Math.max(declaredDays, values.length ? new Set(values).size : (nodes.length ? 1 : 0));
}

function routeNodes(parsed: ReturnType<typeof parseTripContent>) {
  if (!parsed) return [];
  const result: any[] = [];
  const seen = new Set<any>();
  const isNode = (value: any) => value && typeof value === 'object' && ![
    'routes', 'route', 'nodes', 'activities', 'pois', 'points', 'items', 'stops', 'days'
  ].some((key) => Array.isArray(value[key])) && (
    value.name || value.location || value.poi || value.type || value.time
  );
  const visit = (value: any, inheritedDay?: number) => {
    if (!value || typeof value !== 'object') return;
    if (Array.isArray(value)) { value.forEach((item) => visit(item, inheritedDay)); return; }
    const ownDay = Number(value.day ?? value.day_index ?? value.date_index);
    const day = Number.isFinite(ownDay) && ownDay > 0 ? ownDay : inheritedDay;
    if (isNode(value) && !seen.has(value)) {
      seen.add(value);
      result.push({ ...value, ...(day ? { day } : {}) });
    }
    // Historical payloads use all of these names. Traverse them explicitly so
    // nested daily plans are not silently dropped by the profile renderer.
    ['routes', 'route', 'nodes', 'activities', 'pois', 'points', 'items', 'stops'].forEach((key) => {
      if (value[key] !== undefined) visit(value[key], day);
    });
    if (Array.isArray(value.days)) value.days.forEach((entry: any, index: number) => visit(entry, index + 1));
  };
  visit(parsed);
  return result.map((node, index) => ({ ...node, day: Number(node.day) > 0 ? Number(node.day) : 1, _order: index }))
    .sort((a, b) => (a.day - b.day) || (a._order - b._order))
    .map(({ _order, ...node }) => node);
}

// 历史摘要里常带「已为您匹配【…】协同行程」这类机器口吻；检测到就改成事实行。
const MACHINE_COPY = /已为您|为您匹配|协同行程|协同路线|智能规划|多智能体|画像|【|】|深度自然\+/;

function readableTripSummary(parsed: ReturnType<typeof parseTripContent>, nodes: any[], city: string) {
  const raw = textValue(parsed?.summary).trim();
  if (raw && !MACHINE_COPY.test(raw)) return raw;
  const days = routeDays(parsed);
  const facts = [city, days ? `${days} 天` : '', nodes.length ? `${nodes.length} 个停留点` : ''].filter(Boolean);
  return facts.length ? facts.join(' · ') : '';
}

// Content is the stable identity of a shared route. Title/city are only a
// legacy fallback because two different trips can legitimately share both.
function normalizedContent(value: unknown) {
  const raw = textValue(value).trim();
  if (!raw) return '';
  try {
    return JSON.stringify(JSON.parse(raw));
  } catch {
    return raw.replace(/\s+/g, ' ');
  }
}

function tripMetaKey(title: unknown, city: unknown) {
  return `${textValue(title).trim().toLocaleLowerCase()}::${textValue(city).trim().toLocaleLowerCase()}`;
}

function downloadFile(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function buildIcs(trip: ProfileTrip, parsed: ReturnType<typeof parseTripContent>) {
  const title = tripTitle(trip, parsed).replace(/[\r\n]/g, ' ');
  const city = tripCity(trip, parsed);
  const nodes = routeNodes(parsed);
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//OmniRoute//Trip Planner//CN',
    'CALSCALE:GREGORIAN',
    'BEGIN:VEVENT',
    `UID:omniroute-${trip.ID}@omniroute`,
    `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')}`,
    `SUMMARY:${title}${city ? ` · ${city}` : ''}`,
    `DESCRIPTION:${nodes.map((node) => textValue(node?.name || node?.location)).filter(Boolean).join('、')}`,
    'END:VEVENT',
    'END:VCALENDAR',
  ];
  return lines.join('\r\n');
}

function avatarSource(user: UserProfile) {
  if (user.avatarUrl) return assetUrl(user.avatarUrl);
  return generatedAvatarUrl(user.avatarSeed || user.username, user.nickname || user.username);
}

function Avatar({ src, name, className }: { src: string; name: string; className: string }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => { setFailed(false); setLoaded(false); }, [src]);
  return (
    <div className={`relative flex items-center justify-center overflow-hidden bg-paper-hover ${className}`}>
      <span aria-hidden="true" className={`text-lg font-bold text-ink-3 transition-opacity ${loaded ? 'opacity-0' : 'opacity-100'}`}>{name.trim().slice(0, 1) || '旅'}</span>
      {!failed && <img src={src} alt="" className={`absolute inset-0 z-[1] h-full w-full object-cover transition-opacity duration-300 ${loaded ? 'opacity-100' : 'opacity-0'}`} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />}
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex items-start gap-3 border border-rose-200 bg-rose-50 px-4 py-4 text-rose-700" role="alert">
      <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-bold">暂时无法载入这部分内容</p>
        <p className="mt-1 text-xs leading-5 text-rose-600">{message}</p>
        <button type="button" onClick={onRetry} className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-2 text-xs font-bold text-rose-700 shadow-sm ring-1 ring-rose-200 transition hover:bg-rose-100">
          <RefreshCw className="h-3.5 w-3.5" /> 重试
        </button>
      </div>
    </div>
  );
}

function EmptyState({ hasFilter, onContinue }: { hasFilter: boolean; onContinue?: () => void }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-paper-card px-5 py-14 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-paper-hover text-ink-3">
        {hasFilter ? <Search className="h-6 w-6" /> : <MapPin className="h-6 w-6" />}
      </div>
      <p className="mt-4 text-sm font-black text-ink">{hasFilter ? '没有匹配的路书' : '你的第一本路书还在等你'}</p>
      <p className="mx-auto mt-1 max-w-sm text-xs leading-5 text-ink-2">
        {hasFilter ? '换一个关键词或筛选条件试试。' : '描述一次旅行诉求，它会变成可执行的路线。'}
      </p>
      {!hasFilter && onContinue && (
        <button type="button" onClick={onContinue} className="mt-5 inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-xs font-bold text-white transition hover:bg-accent-ink">
          <Play className="h-4 w-4" /> 开始一次规划
        </button>
      )}
    </div>
  );
}

export default function ProfileScreen({
  currentUser,
  onBack,
  onProfileChange,
  onContinuePlanning,
  roomCode,
}: {
  currentUser: UserProfile;
  onBack: () => void;
  onProfileChange: (patch: Partial<UserProfile>) => void;
  onContinuePlanning?: () => void;
  // 当前房间号：只有传了它，特征图谱才能算「房间共同交集」。
  roomCode?: string;
}) {
  const [trips, setTrips] = useState<ProfileTrip[]>([]);
  const [posts, setPosts] = useState<CommunityPostItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState('');
  const [tripError, setTripError] = useState('');
  const [socialError, setSocialError] = useState('');
  const [tripSearch, setTripSearch] = useState('');
  const [tripFilter, setTripFilter] = useState<TripFilter>('all');
  const [expandedTrip, setExpandedTrip] = useState<string | null>(null);
  const [showUploader, setShowUploader] = useState(false);
  const [showEditor, setShowEditor] = useState(false);
  // 编辑资料弹窗打开时把焦点锁在弹窗内，关闭后还给「编辑」按钮
  const editorDialogRef = useFocusTrap<HTMLDivElement>(showEditor);
  const [editNickname, setEditNickname] = useState(currentUser.nickname || currentUser.username);
  const [editSignature, setEditSignature] = useState(currentUser.signature || '');
  const [profileSaving, setProfileSaving] = useState(false);
  const [toast, setToast] = useState('');
  const [avatarUrl, setAvatarUrl] = useState(currentUser.avatarUrl || '');
  const [avatarSaving, setAvatarSaving] = useState(false);
  const [learningQuality, setLearningQuality] = useState<LearningQuality | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setTripError('');
    setSocialError('');
    const [tripResult, postResult] = await Promise.allSettled([
      apiJson<TripData>(`/api/user/trips?user_id=${encodeURIComponent(currentUser.id)}`),
      apiJson<PostsData>(`/api/community/list?user_id=${encodeURIComponent(currentUser.id)}`),
    ]);
    if (tripResult.status === 'fulfilled') setTrips(Array.isArray(tripResult.value.trips) ? tripResult.value.trips : []);
    else setTripError(tripResult.reason instanceof Error ? tripResult.reason.message : '路书服务暂时不可用');
    if (postResult.status === 'fulfilled') setPosts(Array.isArray(postResult.value.posts) ? postResult.value.posts : []);
    else setSocialError(postResult.reason instanceof Error ? postResult.reason.message : '社区数据暂时不可用');
    if (tripResult.status === 'rejected' && postResult.status === 'rejected') setErrorMsg('网关暂时不可达，请确认服务已启动后重试。');
    setLoading(false);
    void apiJson<any>(`/api/v1/planning/quality`)
      .then((response) => setLearningQuality((response?.data || response) as LearningQuality))
      .catch(() => setLearningQuality(null));
  }, [currentUser.id]);

  useEffect(() => { void loadData(); }, [loadData]);
  useEffect(() => {
    setAvatarUrl(currentUser.avatarUrl || '');
    setEditNickname(currentUser.nickname || currentUser.username);
    setEditSignature(currentUser.signature || '');
  }, [currentUser.avatarUrl, currentUser.nickname, currentUser.signature, currentUser.username]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), 2800);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const sharedContentKeys = useMemo(() => new Set(posts.map((post) => normalizedContent(post.content)).filter(Boolean)), [posts]);
  const sharedLegacyMetaKeys = useMemo(() => new Set(posts.filter((post) => !normalizedContent(post.content)).map((post) => tripMetaKey(post.title, post.dest_city))), [posts]);
  const isTripShared = useCallback((trip: ProfileTrip) => {
    const content = normalizedContent(trip.Content);
    if (content) return sharedContentKeys.has(content);
    return sharedLegacyMetaKeys.has(tripMetaKey(trip.Title, trip.DestCity));
  }, [sharedContentKeys, sharedLegacyMetaKeys]);
  const sharedCount = useMemo(() => trips.filter((trip) => isTripShared(trip)).length, [isTripShared, trips]);
  const destinations = useMemo(() => new Set(trips.map((trip) => trip.DestCity).filter(Boolean)).size, [trips]);
  const filteredTrips = useMemo(() => {
    const query = tripSearch.trim().toLocaleLowerCase();
    return trips.filter((trip) => {
      const parsed = parseTripContent(textValue(trip.Content));
      const haystack = `${tripTitle(trip, parsed)} ${tripCity(trip, parsed)} ${trip.Content || ''}`.toLocaleLowerCase();
      if (query && !haystack.includes(query)) return false;
       if (tripFilter === 'shared' && !isTripShared(trip)) return false;
      if (tripFilter === 'recent') {
        const date = new Date(trip.CreatedAt).getTime();
        if (!Number.isFinite(date) || Date.now() - date > 30 * 24 * 60 * 60 * 1000) return false;
      }
      return true;
    });
  }, [isTripShared, tripFilter, tripSearch, trips]);
  const latestTrip = trips[0];

  const saveProfile = async () => {
    const nickname = editNickname.trim();
    if (!nickname) { setErrorMsg('昵称不能为空'); return; }
    setProfileSaving(true);
    setErrorMsg('');
    try {
      await apiJson(`/api/user/profile`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, nickname, signature: editSignature.trim() }),
      });
      onProfileChange({ nickname, signature: editSignature.trim() });
      setShowEditor(false);
      setToast('个人资料已更新');
    } catch (error) {
      setErrorMsg(error instanceof Error ? error.message : '资料保存失败，请稍后重试');
    } finally {
      setProfileSaving(false);
    }
  };

  const saveAvatar = async (url: string) => {
    // AvatarUploader 已完成上传和数据库写入，这里只同步当前用户态，避免重复请求。
    setAvatarSaving(true);
    setAvatarUrl(url);
    onProfileChange({ avatarUrl: url });
    setShowUploader(false);
    setAvatarSaving(false);
    setToast('头像已更新');
  };

  const publishTrip = async (trip: ProfileTrip) => {
    const parsed = parseTripContent(textValue(trip.Content));
    const title = tripTitle(trip, parsed);
    try {
      await apiJson(`/api/community/post`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, title, content: trip.Content, dest_city: tripCity(trip, parsed), tags: ['路书', '智能规划'] }),
      });
      setPosts((current) => [{ id: `local-${trip.ID}`, user_id: currentUser.id, author: currentUser.nickname, author_avatar: currentUser.avatarSeed, author_avatar_url: avatarUrl, title, content: trip.Content, dest_city: tripCity(trip, parsed), likes: 0, comments: 0, favorites: 0, created_at: new Date().toISOString() }, ...current]);
      setToast('已分享到同行者社区');
    } catch (error) {
      setErrorMsg(error instanceof Error ? error.message : '分享失败，请稍后重试');
    }
  };

  const exportTrip = (trip: ProfileTrip, format: 'json' | 'markdown' | 'ics') => {
    const parsed = parseTripContent(textValue(trip.Content));
    const title = tripTitle(trip, parsed);
    if (format === 'json') downloadFile(`${title}.json`, JSON.stringify(parsed || { content: trip.Content }, null, 2), 'application/json;charset=utf-8');
    if (format === 'markdown') downloadFile(`${title}.md`, buildDiaryMarkdown(routeNodes(parsed), { city: tripCity(trip, parsed) }), 'text/markdown;charset=utf-8');
    if (format === 'ics') downloadFile(`${title}.ics`, buildIcs(trip, parsed), 'text/calendar;charset=utf-8');
    setToast(format === 'json' ? '路书 JSON 已导出' : format === 'ics' ? '日历文件已导出' : '旅行日记已导出');
  };

  const displayUser = { ...currentUser, avatarUrl };
  const stats: Array<{ label: string; value: number; Icon: React.ComponentType<{ className?: string }> }> = [
    { label: '路书', value: trips.length, Icon: MapPin },
    { label: '已分享', value: sharedCount, Icon: Share2 },
    { label: '社区帖', value: posts.length, Icon: MessageCircle },
    { label: '目的地', value: destinations, Icon: Compass },
  ];
  const totalNodes = useMemo(() => trips.reduce((sum, trip) => sum + routeNodes(parseTripContent(textValue(trip.Content))).length, 0), [trips]);
  const profileCompletion = Math.min(100, 30 + (currentUser.nickname ? 20 : 0) + (currentUser.signature ? 15 : 0) + (avatarUrl ? 15 : 0) + (trips.length ? 20 : 0));
  const nextAction = trips.length === 0
    ? { eyebrow: '第一步', title: '先做一份属于你的路书', detail: '告诉规划助手你的节奏、预算和在意的事。', action: '开始规划' }
    : posts.length === 0
      ? { eyebrow: '下一步', title: '把满意的路线分享给同行者', detail: '真实反馈会让下一次推荐更贴合你的偏好。', action: '查看我的路书' }
      : { eyebrow: '最近', title: '继续打磨最近的路线', detail: `已积累 ${totalNodes} 个路线节点，可以接着调整最近的安排。`, action: '查看我的路书' };

  return (
    <main className="profile-page min-h-screen w-full bg-paper font-sans text-ink">
      <div className="portal-header border-b border-line bg-paper-card/95 backdrop-blur">
        <div className="portal-container mx-auto flex w-full max-w-[1180px] items-center justify-between gap-4 px-4 py-3 sm:px-8">
          <button type="button" onClick={onBack} className="inline-flex min-h-10 items-center gap-2 rounded-lg px-2.5 text-sm font-semibold text-ink-2 transition hover:bg-paper-hover hover:text-ink"><ArrowLeft className="h-4 w-4" /> <span>返回大厅</span></button>
          <div className="flex items-center gap-2 text-right"><span className="hidden text-[11px] font-semibold text-ink-3 sm:inline">个人主页</span>{onContinuePlanning && <button type="button" onClick={onContinuePlanning} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-accent px-3.5 text-xs font-semibold text-white transition hover:bg-accent-ink"><Play className="h-4 w-4" /> 新建规划</button>}</div>
        </div>
      </div>

      <div className="portal-container mx-auto w-full max-w-[1180px] px-4 py-6 sm:px-8 sm:py-8">
        <section className={`${panel} profile-hero overflow-hidden`} aria-labelledby="profile-heading">
          <div className="grid gap-6 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
            <div className="flex min-w-0 items-center gap-4 sm:gap-5"><div className="relative shrink-0"><Avatar src={avatarSource(displayUser)} name={currentUser.nickname || currentUser.username} className="h-20 w-20 rounded-2xl border-4 border-line-soft shadow-sm sm:h-24 sm:w-24" /></div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h1 id="profile-heading" className="truncate text-2xl font-bold tracking-tight text-ink sm:text-3xl">{currentUser.nickname || currentUser.username}</h1><button type="button" onClick={() => { setEditNickname(currentUser.nickname || currentUser.username); setEditSignature(currentUser.signature || ''); setShowEditor(true); }} className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-ink-3 transition hover:bg-paper-hover hover:text-ink" title="编辑资料" aria-label="编辑资料"><Edit3 className="h-4 w-4" /></button><button type="button" onClick={() => setShowUploader(true)} disabled={avatarSaving} className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-[11px] font-semibold text-ink-3 transition hover:bg-paper-hover hover:text-ink disabled:opacity-60" title="更换头像" aria-label="更换头像"><Camera className="h-3.5 w-3.5" /> 更换头像</button></div><p className="mt-0.5 truncate text-xs font-semibold text-ink-3">@{currentUser.username}</p><p className="mt-2 line-clamp-2 max-w-xl text-sm leading-6 text-ink-2">{currentUser.signature || '还没有写下旅行宣言，去记录下一段远方吧。'}</p><div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-semibold text-ink-3"><span className="inline-flex items-center gap-1"><Check className="h-3.5 w-3.5 text-good" /> 偏好已记录</span><span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" /> 随行程更新</span></div></div></div>
            <div className="profile-stats grid grid-cols-2 gap-3 sm:grid-cols-4 lg:min-w-[430px]">{stats.map(({ label, value, Icon }) => <div key={label} className="rounded-lg bg-paper-hover px-4 py-3 text-center sm:px-3"><Icon className="mx-auto h-4 w-4 text-ink-3" /><strong className="mt-1 block text-xl font-bold text-ink">{value}</strong><span className="text-[10px] font-semibold text-ink-3">{label}</span></div>)}</div>
          </div>
        </section>

        {errorMsg && <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-line bg-risk-soft px-4 py-3 text-xs font-semibold text-risk" role="alert"><span className="inline-flex min-w-0 items-center gap-2"><AlertCircle className="h-4 w-4 shrink-0" />{errorMsg}</span><button type="button" onClick={() => setErrorMsg('')} className="rounded p-1 hover:bg-paper-hover" title="关闭提示" aria-label="关闭提示"><X className="h-4 w-4" /></button></div>}

        <section className="profile-next-step" aria-labelledby="next-step-heading">
          <div className="profile-next-step-copy">
            <span className="profile-kicker">{nextAction.eyebrow}</span>
            <h2 id="next-step-heading">{nextAction.title}</h2>
            <p>{nextAction.detail}</p>
          </div>
          <div className="profile-next-step-progress" aria-label={`资料完整度 ${profileCompletion}%`}>
            <div className="profile-progress-ring" style={{ '--profile-progress': `${profileCompletion}%` } as React.CSSProperties}><span>{profileCompletion}<small>%</small></span></div>
            <span>资料完整度</span>
          </div>
          <button type="button" onClick={trips.length === 0 ? onContinuePlanning : () => document.getElementById('trips-heading')?.scrollIntoView({ behavior: 'smooth', block: 'start' })} className="profile-next-step-action">
            {trips.length === 0 ? <Play className="h-4 w-4" /> : <MapPin className="h-4 w-4" />}{nextAction.action}
          </button>
        </section>

        {/* 个人旅行特征图谱：只读派生，只按会话里的账号返回，别人看不到。 */}
        <UserTraitGraphCard className="mt-6" roomCode={roomCode} />

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
          <section aria-labelledby="trips-heading"><div className="mb-3 flex flex-wrap items-end justify-between gap-3"><div><h2 id="trips-heading" className="text-xl font-bold tracking-tight text-ink">我的路书</h2></div><span className="text-xs font-semibold text-ink-3">{filteredTrips.length} / {trips.length} 条</span></div><div className={`${panel} p-3 sm:p-4`}><div className="flex flex-col gap-3 md:flex-row md:items-center"><label className="relative min-w-0 flex-1"><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" /><input aria-label="搜索我的路书" value={tripSearch} onChange={(event) => setTripSearch(event.target.value)} placeholder="搜索标题、目的地或路线内容" className="h-11 w-full rounded-lg border border-line bg-white pl-9 pr-3 text-sm text-ink outline-none transition placeholder:text-ink-3 focus:border-accent" /></label><div className="flex max-w-full items-center gap-1 overflow-x-auto border border-line bg-paper-hover p-1"><Filter className="ml-2 h-3.5 w-3.5 shrink-0 text-ink-3" />{([['all', '全部'], ['recent', '近 30 天'], ['shared', '已分享']] as const).map(([value, label]) => <button type="button" key={value} onClick={() => setTripFilter(value)} className={`min-h-9 shrink-0 whitespace-nowrap rounded px-3 text-xs font-semibold transition ${tripFilter === value ? 'bg-ink text-white' : 'text-ink-2 hover:bg-white hover:text-ink'}`}>{label}</button>)}</div></div><div className="mt-3 border-t border-line-soft pt-3">{loading ? <div className="flex items-center justify-center gap-2 py-16 text-xs font-semibold text-ink-3"><RefreshCw className="h-5 w-5 animate-spin text-ink-3" />正在同步路书…</div> : tripError ? <ErrorState message={tripError} onRetry={() => void loadData()} /> : filteredTrips.length === 0 ? <EmptyState hasFilter={trips.length > 0 || Boolean(tripSearch || tripFilter !== 'all')} onContinue={onContinuePlanning} /> : <div className="divide-y divide-line-soft">{filteredTrips.map((trip) => { const parsed = parseTripContent(textValue(trip.Content)); const nodes = routeNodes(parsed); const isOpen = expandedTrip === trip.ID; const city = tripCity(trip, parsed); const title = tripTitle(trip, parsed); const days = routeDays(parsed); const isShared = isTripShared(trip); return <article key={trip.ID} className="py-4 first:pt-1 last:pb-1"><div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="min-w-0 max-w-full truncate text-base font-bold text-ink">{title}</h3>{isShared && <span className="inline-flex items-center gap-1 border border-line-soft bg-good-soft px-2 py-0.5 text-[10px] font-semibold text-good"><Check className="h-3 w-3" />已分享</span>}</div><div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-semibold text-ink-3"><span className="inline-flex items-center gap-1 text-ink-2"><MapPin className="h-3.5 w-3.5 text-ink-3" />{city || '未设置目的地'}</span><span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />{tripDate(textValue(trip.CreatedAt))}</span>{nodes.length > 0 && <span>{days || 1} 天 · {nodes.length} 个节点</span>}</div><p className="mt-2 line-clamp-2 text-xs leading-5 text-ink-2">{readableTripSummary(parsed, nodes, city) || textValue(trip.Content, '这本路书还没有摘要。')}</p></div><div className="flex w-full flex-wrap items-center gap-1.5 sm:w-auto sm:max-w-[270px] sm:justify-end"><button type="button" onClick={() => setExpandedTrip(isOpen ? null : trip.ID)} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs font-semibold text-ink-2 transition hover:border-ink-3 hover:bg-paper-hover hover:text-ink">{isOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}{isOpen ? '收起' : '查看路线'}</button><button type="button" onClick={() => void publishTrip(trip)} disabled={isShared} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line bg-paper-hover px-2.5 text-xs font-semibold text-ink-2 transition hover:text-ink disabled:cursor-default disabled:border-line-soft disabled:bg-paper-hover disabled:text-ink-3"><Share2 className="h-3.5 w-3.5" />{isShared ? '已分享' : '分享'}</button><div className="flex items-center gap-1"><button type="button" onClick={() => exportTrip(trip, 'json')} className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-3 transition hover:bg-paper-hover hover:text-ink" title="导出 JSON" aria-label="导出 JSON"><Download className="h-4 w-4" /></button><button type="button" onClick={() => exportTrip(trip, 'markdown')} className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-3 transition hover:bg-paper-hover hover:text-ink" title="导出旅行日记" aria-label="导出旅行日记"><BookOpen className="h-4 w-4" /></button><button type="button" onClick={() => exportTrip(trip, 'ics')} className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-3 transition hover:bg-paper-hover hover:text-ink" title="导出日历" aria-label="导出日历"><FileDown className="h-4 w-4" /></button></div></div></div>{isOpen && <div className="mt-4 border-l border-line pl-4">{nodes.length ? <div className="space-y-4">{nodes.map((node, index) => { const day = Number(node?.day); const previousDay = Number(nodes[index - 1]?.day); const showDay = index === 0 || (Number.isFinite(day) && day !== previousDay); return <div key={`${trip.ID}-${index}`} className="relative"><span className="absolute -left-[25px] top-0.5 flex h-5 w-5 items-center justify-center rounded-full border border-line bg-paper-card text-[10px] font-bold text-ink-2">{index + 1}</span>{showDay && <p className="mb-1 text-[10px] font-semibold tracking-wider text-ink-3">第 {Number.isFinite(day) && day > 0 ? day : 1} 天</p>}<div className="flex flex-wrap items-baseline gap-x-2 gap-y-1"><span className="text-sm font-semibold text-ink">{textValue(node?.name || node?.location, '未命名地点')}</span>{node?.time && <span className="text-[11px] font-semibold text-ink-3">{textValue(node.time).split('|').pop()}</span>}{node?.cost && <span className="text-[11px] font-semibold text-good">{textValue(node.cost)}</span>}</div>{node?.desc && <p className="mt-1 text-xs leading-5 text-ink-2">{textValue(node.desc)}</p>}</div>; })}</div> : <p className="text-xs text-ink-3">这本路书暂时没有结构化节点，但原始内容仍可导出。</p>}</div>}</article>; })}</div>}</div></div></section>

          <aside className="space-y-4">
            <section className={`${panel} p-5`} aria-labelledby="learning-heading">
              <h2 id="learning-heading" className="text-sm font-bold text-ink">旅行偏好</h2>
              <p className="mt-1 text-xs leading-5 text-ink-2">收藏、采用和修改都会记下来，用来调整后续推荐。</p>
              <p className={`mt-2 text-[11px] font-semibold ${learningQuality?.training_ready ? 'text-good' : 'text-warn'}`} data-testid="profile-learning-status">{learningQuality?.training_ready ? '样本已足够' : `还需要更多记录${learningQuality?.learning_readiness?.reason ? `：${learningQuality.learning_readiness.reason}` : ''}`}</p>
              <div className="mt-4 space-y-3">
                <div>
                  <div className="mb-1 flex justify-between text-[11px] font-semibold text-ink-3"><span>资料完整度</span><span>{trips.length ? '已建立' : '待建立'}</span></div>
                  <div className="h-2 rounded bg-paper-hover"><div className={`h-2 rounded bg-ink-2 transition-all ${trips.length ? 'w-4/5' : 'w-1/5'}`} /></div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-lg bg-paper-hover p-3"><p className="text-[10px] font-semibold text-ink-3">最近活动</p><p className="mt-1 text-xs font-bold text-ink">{latestTrip ? tripDate(textValue(latestTrip.CreatedAt)) : '暂无记录'}</p></div>
                  <div className="rounded-lg bg-paper-hover p-3"><p className="text-[10px] font-semibold text-ink-3">有效样本</p><p className="mt-1 text-xs font-bold text-ink">{learningQuality?.learning_readiness?.total_samples ?? learningQuality?.events ?? 0} 条</p></div>
                </div>
              </div>
            </section>
            <section className={`${panel} p-5`} aria-labelledby="social-heading">
              <div className="flex items-center justify-between gap-2"><h2 id="social-heading" className="text-sm font-bold text-ink">社区足迹</h2><MessageCircle className="h-4 w-4 text-ink-3" /></div>
              {socialError ? <div className="mt-3"><ErrorState message={socialError} onRetry={() => void loadData()} /></div> : posts.length ? <div className="mt-3 space-y-3">{posts.slice(0, 3).map((post) => <div key={post.id} className="border-l border-line pl-3"><p className="line-clamp-1 text-xs font-semibold text-ink">{post.title || '分享了一条路线'}</p><p className="mt-1 text-[11px] font-semibold text-ink-3">{post.likes || 0} 赞 · {post.comments || 0} 条评论</p></div>)}</div> : <p className="mt-3 text-xs leading-5 text-ink-2">分享一本路书，和同行者一起把路线打磨得更好。</p>}
            </section>
            {currentUser.isAdmin && <a href="/admin" className={`${panel} group flex items-center justify-between gap-3 p-5 transition hover:border-ink-3`}><span className="min-w-0"><span className="flex items-center gap-2 text-sm font-bold text-ink"><ShieldCheck className="h-4 w-4 text-accent" />运维看板</span><span className="mt-1 block text-xs leading-5 text-ink-2">服务健康、用户与安全事件的聚合视图，仅管理员可见。</span></span><ArrowLeft className="h-4 w-4 shrink-0 rotate-180 text-ink-3 transition group-hover:text-ink" /></a>}
            <AccountSecurity className={`${panel} p-5`} />
            {currentUser.isGuest && <GuestAccountPanel user={currentUser} onUpgraded={(patch) => { onProfileChange(patch); setToast('已转为正式账号，行程与偏好都保留在原账号上'); }} className={`${panel} p-5`} />}
          </aside>
        </div>
      </div>

      {showUploader && <AvatarUploader userId={String(currentUser.id)} onClose={() => setShowUploader(false)} onSuccess={saveAvatar} />}
      {showEditor && <div ref={editorDialogRef} className="fixed inset-0 z-[130] flex items-center justify-center bg-backdrop p-4" role="dialog" aria-modal="true" aria-labelledby="edit-profile-title"><div className="w-full max-w-md rounded-xl border border-line bg-paper-card p-5 shadow-xl sm:p-6"><div className="flex items-center justify-between"><h2 id="edit-profile-title" className="text-lg font-bold text-ink">编辑个人资料</h2><button type="button" onClick={() => setShowEditor(false)} className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-ink-3 hover:bg-paper-hover hover:text-ink" title="关闭" aria-label="关闭"><X className="h-5 w-5" /></button></div><div className="mt-5 space-y-4"><label className="block"><span className="mb-1.5 block text-xs font-semibold text-ink-2">昵称</span><input value={editNickname} maxLength={40} onChange={(event) => setEditNickname(event.target.value)} className="h-11 w-full rounded-lg border border-line bg-white px-3 text-sm text-ink outline-none focus:border-accent" /></label><label className="block"><span className="mb-1.5 block text-xs font-semibold text-ink-2">个性签名</span><textarea value={editSignature} maxLength={160} rows={3} onChange={(event) => setEditSignature(event.target.value)} className="w-full resize-none rounded-lg border border-line bg-white px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-accent" /></label><div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:justify-end"><button type="button" onClick={() => setShowEditor(false)} className="min-h-11 rounded-lg px-4 text-sm font-semibold text-ink-2 hover:bg-paper-hover">取消</button><button type="button" onClick={() => void saveProfile()} disabled={profileSaving} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-accent px-5 text-sm font-semibold text-white transition hover:bg-accent-ink disabled:opacity-60">{profileSaving && <RefreshCw className="h-4 w-4 animate-spin" />}保存资料</button></div></div></div></div>}
      {toast && <div className="fixed bottom-5 left-1/2 z-[200] flex -translate-x-1/2 items-center gap-2 rounded-lg border border-line bg-paper-card px-4 py-3 text-xs font-semibold text-ink shadow-xl" role="status"><Check className="h-4 w-4 text-good" />{toast}</div>}
    </main>
  );
}
