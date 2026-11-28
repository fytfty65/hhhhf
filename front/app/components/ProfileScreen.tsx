'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { generatedAvatarUrl } from '../lib/avatarArt';
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Camera,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  Download,
  Edit3,
  FileDown,
  MapPin,
  MessageCircle,
  Play,
  Plus,
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
import JourneySeasonSelect from './JourneySeasonSelect';
import WebGuide from './WebGuide';
import OmniLogo from './OmniLogo';
import { useJourneySeason } from './JourneySeasonProvider';

type TripFilter = 'all' | 'recent' | 'shared';
type TripData = { trips?: ProfileTrip[] };
type PostsData = { posts?: CommunityPostItem[] };
type LearningQuality = { training_ready?: boolean; events?: number; learning_readiness?: { reason?: string; total_samples?: number; minimum_total_samples?: number } };

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
    <div className="pj-error" role="alert">
      <AlertCircle aria-hidden="true" />
      <div>
        <p>暂时无法载入这部分内容</p>
        <small>{message}</small>
      </div>
      <button type="button" onClick={onRetry}>重试</button>
    </div>
  );
}

function EmptyState({ hasFilter, onContinue }: { hasFilter: boolean; onContinue?: () => void }) {
  return (
    <div className="pj-empty">
      <div className="pj-empty-mark" aria-hidden="true">
        {hasFilter ? <Search /> : <MapPin />}
      </div>
      <h3>{hasFilter ? '没有匹配的路书' : '你的第一本路书还在等你'}</h3>
      <p>{hasFilter ? '换一个关键词或筛选条件，再翻一翻这本旅行档案。' : '描述一次旅行诉求，它会变成可执行的路线，也会成为这本手札的第一页。'}</p>
      {!hasFilter && onContinue && (
        <button type="button" onClick={onContinue}>
          <Play /> 开始一次规划
        </button>
      )}
    </div>
  );
}

function CoverStamp() {
  const pathId = React.useId();
  return (
    <div className="pj-cover-stamp" aria-hidden="true">
      <svg viewBox="0 0 120 120">
        <defs>
          <path id={pathId} d="M60,60 m-45,0 a45,45 0 1,1 90,0 a45,45 0 1,1 -90,0" />
        </defs>
        <circle cx="60" cy="60" r="55" fill="none" stroke="rgba(31,35,40,.26)" strokeWidth="1.1" />
        <circle cx="60" cy="60" r="46" fill="none" stroke="rgba(31,35,40,.17)" strokeWidth=".8" />
        <circle cx="60" cy="60" r="41" fill="none" stroke="rgba(31,35,40,.14)" strokeWidth=".7" strokeDasharray="2 4" />
        <text fill="rgba(31,35,40,.32)" fontSize="6.8" fontWeight="600" letterSpacing=".9">
          <textPath href={`#${pathId}`} startOffset="4%">OMNIROUTE POST · FIELD NOTES · 旅人手札 ·</textPath>
        </text>
        <text x="60" y="55" fill="rgba(31,35,40,.38)" fontFamily="'Noto Serif SC', serif" fontSize="16" textAnchor="middle" letterSpacing="2">同行</text>
        <text x="60" y="69" fill="rgba(31,35,40,.26)" fontFamily="Georgia, serif" fontSize="6.5" textAnchor="middle" letterSpacing=".7">THE LONG WAY HOME</text>
        {[76, 82, 88, 94].map((y) => (
          <path
            key={y}
            d={`M14 ${y} q 9 -4 18 0 t 18 0 t 18 0 t 18 0 t 18 0`}
            fill="none"
            stroke="rgba(31,35,40,.16)"
            strokeWidth="1"
          />
        ))}
      </svg>
    </div>
  );
}

function CoverRoute() {
  return (
    <svg className="pj-cover-route" viewBox="0 0 540 140" aria-hidden="true">
      <path d="M4 120 C 112 48, 222 134, 336 64 S 498 24, 534 18" />
      <circle cx="4" cy="120" r="2.2" />
      <circle cx="173" cy="76" r="1.8" />
      <circle cx="336" cy="64" r="1.8" />
      <circle cx="534" cy="18" r="2.2" />
      <text x="24" y="112">THE WAY WE KEEP</text>
      <text x="405" y="44">下一站 · 共赴远方</text>
    </svg>
  );
}

export default function ProfileScreen({
  currentUser,
  onBack,
  onProfileChange,
  onContinuePlanning,
  onOpenCommunity,
  roomCode,
}: {
  currentUser: UserProfile;
  onBack: () => void;
  onProfileChange: (patch: Partial<UserProfile>) => void;
  onContinuePlanning?: () => void;
  onOpenCommunity?: () => void;
  // 当前房间号：只有传了它，特征图谱才能算「房间共同交集」。
  roomCode?: string;
}) {
  const { season, story } = useJourneySeason();
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
  const latestParsed = latestTrip ? parseTripContent(textValue(latestTrip.Content)) : null;
  const latestNodes = latestTrip ? routeNodes(latestParsed) : [];
  const latestTitle = latestTrip ? tripTitle(latestTrip, latestParsed) : '';
  const stats: Array<{ label: string; value: number; Icon: React.ComponentType<{ className?: string }>; target: string }> = [
    { label: '路书', value: trips.length, Icon: MapPin, target: 'trips-heading' },
    { label: '已分享', value: sharedCount, Icon: Share2, target: 'trips-heading' },
    { label: '社区帖', value: posts.length, Icon: MessageCircle, target: 'community-footprint' },
    { label: '目的地', value: destinations, Icon: Compass, target: 'trips-heading' },
  ];
  const totalNodes = trips.reduce((sum, trip) => sum + routeNodes(parseTripContent(textValue(trip.Content))).length, 0);
  const profileCompletion = Math.min(100, 30 + (currentUser.nickname ? 20 : 0) + (currentUser.signature ? 15 : 0) + (avatarUrl ? 15 : 0) + (trips.length ? 20 : 0));
  const nextAction = trips.length === 0
    ? { eyebrow: '第一步', code: 'START', title: '先做一份属于你的路书', detail: '告诉规划助手你的节奏、预算和在意的事。', action: '开始规划' }
    : { eyebrow: '最近编辑', code: 'CONTINUE', title: '继续打磨最近的路线', detail: `${latestTitle} · 已积累 ${latestNodes.length || totalNodes} 个路线节点，可随时返回继续微调。`, action: '查看我的路书' };
  const learningNote = learningQuality?.training_ready
    ? '样本已足够'
    : `还需要更多记录：${learningQuality?.learning_readiness?.reason || '总样本不足'}`;
  const scrollToSection = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <main className="profile-page profile-journal min-h-screen w-full" data-season={season}>
      <div className="pj-canvas">
        <div className="pj-frame">
          <header className="pj-header">
            <div className="pj-header-left">
              <button type="button" onClick={onBack} className="pj-back" title="返回大厅">
                <ArrowLeft aria-hidden="true" />
                <span>返回大厅</span>
              </button>
              <span className="pj-header-divider" aria-hidden="true" />
              <div className="pj-brand">
                <OmniLogo className="pj-brand-logo" />
                <div className="pj-brand-text">
                  <strong>OmniRoute</strong>
                  <span>个人主页</span>
                </div>
              </div>
            </div>
            <div className="pj-header-right">
              <div className="pj-guide-slot"><WebGuide startAt="profile" /></div>
              <JourneySeasonSelect />
              <button
                type="button"
                className="pj-header-avatar"
                onClick={() => scrollToSection('profile-heading')}
                aria-label="回到个人档案"
                title="回到个人档案"
              >
                <Avatar src={avatarSource(displayUser)} name={currentUser.nickname || currentUser.username} className="pj-avatar-img" />
              </button>
              {onContinuePlanning && (
                <button type="button" onClick={onContinuePlanning} className="pj-new-plan">
                  <Plus aria-hidden="true" />
                  <span>新建规划</span>
                </button>
              )}
            </div>
          </header>

          <div className="pj-content">
            {errorMsg && (
              <div className="pj-error" role="alert">
                <AlertCircle aria-hidden="true" />
                <div><p>{errorMsg}</p></div>
                <button type="button" onClick={() => setErrorMsg('')}>关闭</button>
              </div>
            )}

            <section className="pj-passport" aria-labelledby="profile-heading">
              <div className="profile-landscape pj-cover">
                <CoverRoute />
                <div className="pj-cover-copy">
                  <span className="pj-cover-kicker">{story.english} / THE TRAVEL JOURNAL</span>
                  <div className="pj-cover-title">
                    <p className="pj-cover-poem">{story.journal}</p>
                    <span className="pj-cover-edition" aria-hidden="true">{story.edition}</span>
                  </div>
                </div>
              </div>
              <CoverStamp />

              <div className="pj-passport-body">
                <div className="pj-identity">
                  <button
                    type="button"
                    className="pj-avatar-button"
                    onClick={() => setShowUploader(true)}
                    disabled={avatarSaving}
                    title="更换头像"
                    aria-label="更换头像"
                  >
                    <Avatar src={avatarSource(displayUser)} name={currentUser.nickname || currentUser.username} className="pj-avatar-img" />
                    <span className="pj-avatar-camera" aria-hidden="true"><Camera /></span>
                  </button>
                  <div className="pj-identity-copy">
                    <div className="pj-name-row">
                      <h1 id="profile-heading">{currentUser.nickname || currentUser.username}</h1>
                      <button
                        type="button"
                        className="pj-icon-button"
                        onClick={() => {
                          setEditNickname(currentUser.nickname || currentUser.username);
                          setEditSignature(currentUser.signature || '');
                          setShowEditor(true);
                        }}
                        title="编辑资料"
                        aria-label="编辑资料"
                      >
                        <Edit3 aria-hidden="true" />
                      </button>
                      <button type="button" className="pj-change-avatar" onClick={() => setShowUploader(true)}>
                        <Camera aria-hidden="true" />更换头像
                      </button>
                    </div>
                    <p className="pj-handle">@{currentUser.nickname || currentUser.username}</p>
                    <button
                      type="button"
                      className={`pj-signature${currentUser.signature ? '' : ' is-empty'}`}
                      onClick={() => {
                        setEditNickname(currentUser.nickname || currentUser.username);
                        setEditSignature(currentUser.signature || '');
                        setShowEditor(true);
                      }}
                      title="编辑旅行宣言"
                    >
                      {currentUser.signature || '还没有写下旅行宣言，去记录下一段远方吧。'}
                    </button>
                    <div className="pj-status-row">
                      <span className="pj-status-pill"><Check aria-hidden="true" /> 偏好已记录</span>
                      <span className="pj-status-pill"><Clock3 aria-hidden="true" /> 随行程更新</span>
                    </div>
                  </div>
                </div>

                <div className="pj-passport-stats" aria-label="旅行档案数据">
                  {stats.map(({ label, value, Icon, target }) => (
                    <button
                      type="button"
                      key={label}
                      className="pj-stat"
                      onClick={() => scrollToSection(target)}
                      aria-label={`${label} ${value}，前往查看`}
                    >
                      <Icon aria-hidden="true" />
                      <strong>{value}</strong>
                      <span>{label}</span>
                    </button>
                  ))}
                </div>
              </div>
            </section>

            <section className="pj-ticket" aria-labelledby="next-step-heading">
              <div className="pj-ticket-copy">
                <span className="pj-ticket-kicker">{nextAction.eyebrow}<span>{nextAction.code}</span></span>
                <h2 id="next-step-heading">{nextAction.title}</h2>
                <p>{nextAction.detail}</p>
              </div>
              <div className="pj-ticket-side">
                <div className="pj-ticket-progress">
                  <div className="pj-progress-ring" style={{ '--pj-progress': `${profileCompletion}%` } as React.CSSProperties} aria-label={`资料完整度 ${profileCompletion}%`}>
                    <span>{profileCompletion}<small>%</small></span>
                  </div>
                  <span className="pj-progress-label">资料完整度</span>
                </div>
                <button
                  type="button"
                  className="pj-ticket-action"
                  onClick={trips.length === 0 ? onContinuePlanning : () => scrollToSection('trips-heading')}
                >
                  {nextAction.action}
                  <ArrowRight aria-hidden="true" />
                </button>
              </div>
            </section>

            <div className="pj-astrolabe-section">
              {/* 个人旅行特征图谱：只读派生，只按会话里的账号返回，别人看不到。 */}
              <UserTraitGraphCard className="pj-astrolabe" roomCode={roomCode} />
            </div>

            <div className="pj-workspace">
              <section className="pj-ledger" aria-labelledby="trips-heading">
                <div className="pj-section-head">
                  <h2 id="trips-heading">我的路书</h2>
                  <span className="pj-count"><BookOpen aria-hidden="true" />{filteredTrips.length} / {trips.length} 篇</span>
                </div>

                <div className="pj-ledger-tools">
                  <label className="pj-search">
                    <Search aria-hidden="true" />
                    <input
                      data-web-guide="trip-search"
                      aria-label="搜索我的路书"
                      value={tripSearch}
                      onChange={(event) => setTripSearch(event.target.value)}
                      placeholder="搜索标题、目的地或路线内容..."
                    />
                  </label>
                  <div className="pj-segmented" role="group" aria-label="路书筛选">
                    {([['all', '全部'], ['recent', '近 30 天'], ['shared', '已分享']] as const).map(([value, label]) => (
                      <button
                        type="button"
                        key={value}
                        aria-pressed={tripFilter === value}
                        onClick={() => setTripFilter(value)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="pj-trip-list">
                  {loading ? (
                    <div className="pj-skeleton"><RefreshCw aria-hidden="true" />正在同步路书…</div>
                  ) : tripError ? (
                    <ErrorState message={tripError} onRetry={() => void loadData()} />
                  ) : filteredTrips.length === 0 ? (
                    <EmptyState hasFilter={trips.length > 0 || Boolean(tripSearch || tripFilter !== 'all')} onContinue={onContinuePlanning} />
                  ) : filteredTrips.map((trip) => {
                    const parsed = parseTripContent(textValue(trip.Content));
                    const nodes = routeNodes(parsed);
                    const isOpen = expandedTrip === trip.ID;
                    const city = tripCity(trip, parsed);
                    const title = tripTitle(trip, parsed);
                    const days = routeDays(parsed);
                    const isShared = isTripShared(trip);
                    return (
                      <article key={trip.ID} className={`pj-trip-card${isOpen ? ' is-open' : ''}`}>
                        <div className="pj-trip-head">
                          <div className="pj-trip-title-row">
                            <h3>{title}</h3>
                            {isShared && <span className="pj-seal"><Check aria-hidden="true" />已分享</span>}
                          </div>
                          <div className="pj-trip-actions">
                            <button
                              type="button"
                              className="pj-ghost-btn"
                              onClick={() => setExpandedTrip(isOpen ? null : trip.ID)}
                              aria-expanded={isOpen}
                            >
                              {isOpen ? '收起' : '查看路线'}
                              {isOpen ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
                            </button>
                            <button
                              type="button"
                              className={`pj-share-btn${isShared ? ' is-shared' : ''}`}
                              onClick={() => void publishTrip(trip)}
                              disabled={isShared}
                            >
                              {isShared ? <Check aria-hidden="true" /> : <Share2 aria-hidden="true" />}
                              {isShared ? '已分享' : '分享'}
                            </button>
                          </div>
                        </div>

                        <div className="pj-trip-meta">
                          <span className="is-city"><MapPin aria-hidden="true" />{city || '未设置目的地'}</span>
                          <i aria-hidden="true" />
                          <span><Clock3 aria-hidden="true" />{tripDate(textValue(trip.CreatedAt))}</span>
                          <i aria-hidden="true" />
                          <span><Compass aria-hidden="true" />{days || 1} 天 · {nodes.length} 个停留点</span>
                        </div>

                        <div className="pj-trip-foot">
                          <div className="pj-node-track" title={nodes.map((node) => textValue(node?.name || node?.location)).filter(Boolean).join(' · ')}>
                            {nodes.length > 0 ? (
                              <>
                                <span className="pj-track-end">起点</span>
                                <span className="pj-track-hub" aria-hidden="true" />
                                <span className="pj-track-line" aria-hidden="true">
                                  {nodes.slice(0, 5).map((node, index) => (
                                    <i key={`${trip.ID}-track-${index}`} />
                                  ))}
                                  {nodes.length > 5 && <em>+{nodes.length - 5}</em>}
                                </span>
                                <span className="pj-track-arrow" aria-hidden="true">▶</span>
                                <span className="pj-track-end">终点</span>
                              </>
                            ) : (
                              <span className="pj-track-node">路线节点待补充</span>
                            )}
                          </div>
                          <div className="pj-trip-tools">
                            <button type="button" className="pj-tool" onClick={() => exportTrip(trip, 'json')} title="导出 JSON" aria-label="导出 JSON">
                              <Download aria-hidden="true" />
                            </button>
                            <button type="button" className="pj-tool" onClick={() => exportTrip(trip, 'markdown')} title="导出旅行日记" aria-label="导出旅行日记">
                              <BookOpen aria-hidden="true" />
                            </button>
                            <button type="button" className="pj-tool" onClick={() => exportTrip(trip, 'ics')} title="导出日历" aria-label="导出日历">
                              <FileDown aria-hidden="true" />
                            </button>
                          </div>
                        </div>

                        {isOpen && (
                          <div className="pj-trip-drawer">
                            {nodes.length ? (
                              <div className="pj-timeline">
                                {nodes.map((node, index) => {
                                  const day = Number(node?.day);
                                  const previousDay = Number(nodes[index - 1]?.day);
                                  const showDay = index === 0 || (Number.isFinite(day) && day !== previousDay);
                                  const time = textValue(node?.time).split('|').pop();
                                  return (
                                    <div key={`${trip.ID}-${index}`} className="pj-timeline-item">
                                      <span className="pj-timeline-dot" aria-hidden="true" />
                                      {showDay && <p className="pj-timeline-day">第 {Number.isFinite(day) && day > 0 ? day : 1} 天</p>}
                                      <h4>
                                        {textValue(node?.name || node?.location, '未命名地点')}
                                        {time && <span>{time}</span>}
                                      </h4>
                                      {node?.desc && <p>{textValue(node.desc)}</p>}
                                    </div>
                                  );
                                })}
                              </div>
                            ) : (
                              <p className="pj-trip-empty-note">这本路书暂时没有结构化节点，但原始内容仍可导出。</p>
                            )}
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>
              </section>

              <aside className="pj-side">
                <section className="pj-side-card" aria-labelledby="learning-heading">
                  <div className="pj-side-head">
                    <h2 id="learning-heading"><Compass aria-hidden="true" />旅行偏好</h2>
                  </div>
                  <p className="pj-side-desc">收藏、采用与修改都会记下来，用来调整后续推荐。</p>
                  <p className={`pj-warn-pill${learningQuality?.training_ready ? ' is-good' : ''}`} data-testid="profile-learning-status">
                    <span aria-hidden="true" />
                    {learningNote}
                  </p>
                  <div className="pj-meter">
                    <div className="pj-meter-labels">
                      <span>资料完整度</span>
                      <strong>{trips.length ? '已建立' : '待建立'}</strong>
                    </div>
                    <div className="pj-meter-track" aria-hidden="true"><span style={{ width: `${profileCompletion}%` }} /></div>
                  </div>
                  <div className="pj-mini-grid">
                    <div className="pj-mini-card">
                      <p>最近活动</p>
                      <strong>{latestTrip ? tripDate(textValue(latestTrip.CreatedAt)) : '暂无记录'}</strong>
                    </div>
                    <div className="pj-mini-card">
                      <p>有效样本</p>
                      <strong>{learningQuality?.learning_readiness?.total_samples ?? learningQuality?.events ?? 0} 条</strong>
                    </div>
                  </div>
                </section>

                <section className="pj-side-card" id="community-footprint" aria-labelledby="social-heading">
                  <div className="pj-side-head">
                    <h2 id="social-heading"><MessageCircle aria-hidden="true" />社区足迹</h2>
                    {onOpenCommunity && (
                      <button type="button" className="pj-side-link" onClick={onOpenCommunity}>
                        前往社区 <ArrowUpRight aria-hidden="true" />
                      </button>
                    )}
                  </div>
                  {socialError ? (
                    <p className="pj-inline-error">{socialError}<button type="button" onClick={() => void loadData()}>重试</button></p>
                  ) : posts.length ? (
                    <div className="pj-footprint-list">
                      {posts.slice(0, 3).map((post) => (
                        <button
                          type="button"
                          key={post.id}
                          className="pj-footprint-item"
                          onClick={onOpenCommunity}
                          disabled={!onOpenCommunity}
                        >
                          <span className="pj-footprint-copy">
                            <span className="pj-footprint-title">{post.title || '分享了一条路线'}</span>
                            <span className="pj-footprint-meta">{post.likes || 0} 赞 · {post.comments || 0} 条评论</span>
                          </span>
                          <ArrowUpRight aria-hidden="true" />
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="pj-side-empty">分享一本路书，和同行者一起把路线打磨得更好。</p>
                  )}
                </section>

                {currentUser.isAdmin && (
                  <a href="/admin" className="pj-admin-link">
                    <span>
                      <strong><ShieldCheck aria-hidden="true" />运维看板</strong>
                      <small>服务健康、用户与安全事件的聚合视图，仅管理员可见。</small>
                    </span>
                    <ArrowRight aria-hidden="true" />
                  </a>
                )}

                <AccountSecurity className="pj-side-card pj-security-card" />
                {currentUser.isGuest && (
                  <GuestAccountPanel
                    user={currentUser}
                    onUpgraded={(patch) => {
                      onProfileChange(patch);
                      setToast('已转为正式账号，行程与偏好都保留在原账号上');
                    }}
                    className="pj-side-card pj-guest-card"
                  />
                )}
              </aside>
            </div>
          </div>
        </div>
      </div>

      {showUploader && <AvatarUploader userId={String(currentUser.id)} onClose={() => setShowUploader(false)} onSuccess={saveAvatar} />}
      {showEditor && (
        <div ref={editorDialogRef} className="pj-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="edit-profile-title">
          <div className="pj-modal">
            <div className="pj-modal-head">
              <h2 id="edit-profile-title">编辑个人资料</h2>
              <button type="button" onClick={() => setShowEditor(false)} className="pj-icon-button" title="关闭" aria-label="关闭">
                <X aria-hidden="true" />
              </button>
            </div>
            <div className="pj-modal-body">
              <label className="pj-modal-field">
                <span>昵称</span>
                <input value={editNickname} maxLength={40} onChange={(event) => setEditNickname(event.target.value)} />
              </label>
              <label className="pj-modal-field">
                <span>个性签名</span>
                <textarea value={editSignature} maxLength={160} rows={3} onChange={(event) => setEditSignature(event.target.value)} />
              </label>
              <div className="pj-modal-actions">
                <button type="button" onClick={() => setShowEditor(false)} className="is-secondary">取消</button>
                <button type="button" onClick={() => void saveProfile()} disabled={profileSaving} className="is-primary">
                  {profileSaving && <RefreshCw aria-hidden="true" />}保存资料
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {toast && (
        <div className="pj-toast" role="status"><Check aria-hidden="true" />{toast}</div>
      )}
    </main>
  );
}
