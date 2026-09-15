'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  Bookmark,
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  Copy,
  Download,
  Flame,
  Hash,
  Heart,
  MapPin,
  MessageCircle,
  Plus,
  RefreshCw,
  Route,
  Search,
  Send,
  Share2,
  ShieldCheck,
  Sparkles,
  Tag,
  X,
} from 'lucide-react';
import { API_BASE, apiJson } from '../lib/utils';
import { buildDiaryMarkdown } from '../lib/tripExport';
import type { CommunityComment, CommunityPostItem, UserProfile } from '../types';

type ViewMode = 'discover' | 'mine' | 'saved';
type SortMode = 'recommended' | 'latest' | 'hot';
type PostsData = { posts?: CommunityPostItem[] };
type TagsData = { tags?: Array<{ tag: string; count: number }> };

// Shared portal surface. The community feed uses the same visual language as
// the profile and planning workspace while retaining dense, scannable rows.
const panel = 'community-panel border border-slate-200/80 bg-white shadow-[0_10px_30px_rgba(15,23,42,0.05)]';

function stringValue(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : value == null ? fallback : String(value);
}

function parseContent(content: string) {
  try {
    const value = JSON.parse(content);
    return value && typeof value === 'object'
      ? value as { summary?: string; city?: string; days?: unknown[]; routes?: any[]; budget?: number; total_budget?: number }
      : null;
  } catch {
    return null;
  }
}

function nodesOf(parsed: ReturnType<typeof parseContent>) {
  return Array.isArray(parsed?.routes) ? parsed.routes : [];
}

function cityOf(post: CommunityPostItem, parsed: ReturnType<typeof parseContent>) {
  return stringValue(post.dest_city) || stringValue(parsed?.city);
}

function postDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '刚刚' : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function avatarOf(post: Pick<CommunityPostItem, 'author_avatar_url' | 'author_avatar' | 'author'>) {
  if (post.author_avatar_url) return `${API_BASE}${post.author_avatar_url}`;
  return `https://api.dicebear.com/9.x/notionists/svg?seed=${encodeURIComponent(post.author_avatar || post.author || 'traveler')}&backgroundColor=fdeed8`;
}

function Avatar({ src, name, className }: { src: string; name: string; className: string }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => { setFailed(false); setLoaded(false); }, [src]);
  return (
    <div className={`relative flex items-center justify-center overflow-hidden bg-orange-100 ${className}`}>
      <span aria-hidden="true" className={`text-sm font-black text-orange-600 transition-opacity ${loaded ? 'opacity-0' : 'opacity-100'}`}>{name.trim().slice(0, 1) || '旅'}</span>
      {!failed && <img src={src} alt="" className={`absolute inset-0 z-[1] h-full w-full object-cover transition-opacity duration-300 ${loaded ? 'opacity-100' : 'opacity-0'}`} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />}
    </div>
  );
}

function fileDownload(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function copyLink(value: string) {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const input = document.createElement('textarea');
    input.value = value;
    input.setAttribute('readonly', 'true');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.appendChild(input);
    input.select();
    const copied = document.execCommand('copy');
    input.remove();
    return copied;
  }
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex items-start gap-3 border border-rose-200 bg-rose-50 p-4 text-rose-700" role="alert">
      <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-bold">社区暂时无法连接</p>
        <p className="mt-1 text-xs leading-5 text-rose-600">{message}</p>
        <button type="button" onClick={onRetry} className="mt-3 inline-flex min-h-9 items-center gap-1.5 bg-white px-3 text-xs font-bold text-rose-700 shadow-sm ring-1 ring-rose-200 hover:bg-rose-100">
          <RefreshCw className="h-3.5 w-3.5" /> 重试
        </button>
      </div>
    </div>
  );
}

export default function CommunityPanel({
  currentUser,
  onBack,
  onAdoptPlanning,
}: {
  currentUser: UserProfile;
  onBack: () => void;
  onAdoptPlanning?: (city: string) => void;
}) {
  const [posts, setPosts] = useState<CommunityPostItem[]>([]);
  const [hotTags, setHotTags] = useState<Array<{ tag: string; count: number }>>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [viewMode, setViewMode] = useState<ViewMode>('discover');
  const [sortMode, setSortMode] = useState<SortMode>('recommended');
  const [activeTag, setActiveTag] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [composerOpen, setComposerOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [city, setCity] = useState('');
  const [content, setContent] = useState('');
  const [composerTags, setComposerTags] = useState('');
  const [publishing, setPublishing] = useState(false);
  const [routeOpen, setRouteOpen] = useState<string | null>(null);
  const [commentsOpen, setCommentsOpen] = useState<string | null>(null);
  const [commentsMap, setCommentsMap] = useState<Record<string, CommunityComment[]>>({});
  const [commentText, setCommentText] = useState('');
  const [replyingTo, setReplyingTo] = useState<CommunityComment | null>(null);
  const [likeBusy, setLikeBusy] = useState<string | null>(null);
  const [favoriteBusy, setFavoriteBusy] = useState<string | null>(null);
  const [favorited, setFavorited] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState('');

  const loadPosts = useCallback(async () => {
    setLoading(true);
    setListError('');
    try {
      // The API's user_id parameter is an ownership filter. Discovery and
      // saved views must load the whole feed so the server can annotate each
      // item with the current user's favorite state.
      const params = new URLSearchParams();
      if (viewMode === 'mine') params.set('user_id', String(currentUser.id));
      if (activeTag) params.set('tag', activeTag);
      const query = params.toString();
      const data = await apiJson<PostsData>(`${API_BASE}/api/community/list${query ? `?${query}` : ''}`);
      const next = Array.isArray(data.posts) ? data.posts : [];
      setPosts(next);
      setFavorited(new Set(next.filter((post) => Boolean(post.favorited)).map((post) => post.id)));
    } catch (error) {
      setListError(error instanceof Error ? error.message : '数据服务暂时不可用');
    } finally {
      setLoading(false);
    }
  }, [activeTag, currentUser.id, viewMode]);

  const loadTags = useCallback(async () => {
    try {
      const data = await apiJson<TagsData>(`${API_BASE}/api/community/tags`);
      setHotTags(Array.isArray(data.tags) ? data.tags : []);
    } catch {
      // 标签属于增强信息，失败时保留主内容流。
    }
  }, []);

  useEffect(() => { void loadPosts(); void loadTags(); }, [loadPosts, loadTags]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), 2800);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const visiblePosts = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase();
    return posts
      .filter((post) => {
        if (viewMode === 'mine' && post.user_id !== currentUser.id) return false;
        if (viewMode === 'saved' && !favorited.has(post.id)) return false;
        if (query) {
          const haystack = `${post.title} ${post.dest_city} ${post.author} ${post.content} ${(post.tags || []).join(' ')}`.toLocaleLowerCase();
          if (!haystack.includes(query)) return false;
        }
        return true;
      })
      .sort((a, b) => {
        if (sortMode === 'latest') return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        if (sortMode === 'hot') return (b.heat || b.likes + (b.comments || 0) * 2 + (b.favorites || 0) * 3) - (a.heat || a.likes + (a.comments || 0) * 2 + (a.favorites || 0) * 3);
        return (b.heat || b.likes * 2 + (b.favorites || 0) * 3) - (a.heat || a.likes * 2 + (a.favorites || 0) * 3);
      });
  }, [currentUser.id, favorited, posts, searchQuery, sortMode, viewMode]);

  const selectTag = (tag: string) => setActiveTag((current) => current === tag ? '' : tag);

  const publish = async () => {
    if (!title.trim() && !content.trim()) return;
    setPublishing(true);
    try {
      const tags = composerTags.split(/[\s,#，]+/).map((tag) => tag.trim()).filter(Boolean).slice(0, 8);
      await apiJson(`${API_BASE}/api/community/post`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, title: title.trim(), content: content.trim(), dest_city: city.trim(), tags }),
      });
      setComposerOpen(false);
      setTitle(''); setCity(''); setContent(''); setComposerTags('');
      setToast('分享已发布');
      await loadPosts();
      await loadTags();
    } catch (error) {
      setToast(error instanceof Error ? error.message : '发布失败，请稍后重试');
    } finally {
      setPublishing(false);
    }
  };

  const toggleLike = async (post: CommunityPostItem) => {
    setLikeBusy(post.id);
    try {
      const data = await apiJson<{ likes?: number }>(`${API_BASE}/api/community/like`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ post_id: post.id }),
      });
      if (typeof data.likes === 'number') setPosts((current) => current.map((item) => item.id === post.id ? { ...item, likes: data.likes as number } : item));
    } catch (error) {
      setToast(error instanceof Error ? error.message : '点赞失败');
    } finally {
      setLikeBusy(null);
    }
  };

  const toggleFavorite = async (post: CommunityPostItem) => {
    setFavoriteBusy(post.id);
    try {
      const data = await apiJson<{ favorites?: number; favorited?: boolean }>(`${API_BASE}/api/community/favorite`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ post_id: post.id, user_id: currentUser.id }),
      });
      setFavorited((current) => { const next = new Set(current); if (data.favorited) next.add(post.id); else next.delete(post.id); return next; });
      if (typeof data.favorites === 'number') setPosts((current) => current.map((item) => item.id === post.id ? { ...item, favorites: data.favorites as number, favorited: Boolean(data.favorited) } : item));
      void apiJson(`${API_BASE}/api/v1/planning/events`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_type: 'plan_favorited', plan_id: post.id, payload: { module: 'community', favorited: Boolean(data.favorited) } }),
      }).catch(() => {});
    } catch (error) {
      setToast(error instanceof Error ? error.message : '收藏失败');
    } finally {
      setFavoriteBusy(null);
    }
  };

  const loadComments = async (postId: string) => {
    try {
      const data = await apiJson<{ comments?: CommunityComment[] }>(`${API_BASE}/api/community/comments?post_id=${encodeURIComponent(postId)}`);
      setCommentsMap((current) => ({ ...current, [postId]: Array.isArray(data.comments) ? data.comments : [] }));
    } catch (error) {
      setToast(error instanceof Error ? error.message : '评论加载失败');
    }
  };

  const toggleComments = (postId: string) => {
    if (commentsOpen === postId) {
      setCommentsOpen(null);
      setReplyingTo(null);
      return;
    }
    setCommentsOpen(postId);
    if (!commentsMap[postId]) void loadComments(postId);
  };

  const sendComment = async (postId: string) => {
    const value = commentText.trim();
    if (!value) return;
    try {
      await apiJson(`${API_BASE}/api/community/comment`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ post_id: postId, user_id: currentUser.id, content: value, parent_id: replyingTo?.id || '' }),
      });
      setCommentText(''); setReplyingTo(null);
      await loadComments(postId);
      setPosts((current) => current.map((post) => post.id === postId ? { ...post, comments: (post.comments || 0) + 1 } : post));
      setToast('评论已发送');
    } catch (error) {
      setToast(error instanceof Error ? error.message : '评论失败，请稍后重试');
    }
  };

  const adopt = async (post: CommunityPostItem, parsed: ReturnType<typeof parseContent>) => {
    const city = cityOf(post, parsed);
    const nodes = nodesOf(parsed);
    if (!parsed || !nodes.length) {
      setToast('这篇分享没有可复制的结构化路书');
      return;
    }
    try {
      await apiJson(`${API_BASE}/api/user/trip`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: currentUser.id, title: post.title || `${city || '目的地'} 行程`, dest_city: city, content: post.content }),
      });
      setToast('已加入我的路书，正在打开规划工作区');
      onAdoptPlanning?.(city);
    } catch (error) {
      setToast(error instanceof Error ? error.message : '复制路书失败，请稍后重试');
    }
  };

  const exportPost = (post: CommunityPostItem, format: 'json' | 'markdown') => {
    const parsed = parseContent(post.content);
    const city = cityOf(post, parsed);
    const safeTitle = (post.title || `${city || '路线'} 路书`).replace(/[\\/:*?"<>|]/g, '-');
    if (format === 'json') fileDownload(`${safeTitle}.json`, JSON.stringify(parsed || { content: post.content }, null, 2), 'application/json;charset=utf-8');
    else fileDownload(`${safeTitle}.md`, buildDiaryMarkdown(nodesOf(parsed), { city }), 'text/markdown;charset=utf-8');
    setToast(format === 'json' ? '路书数据已导出' : '旅行日记已导出');
  };

  const sharePost = async (post: CommunityPostItem) => {
    const url = `${window.location.origin}${window.location.pathname}?post=${encodeURIComponent(post.id)}`;
    try {
      if (navigator.share) {
        await navigator.share({ title: post.title || 'OmniRoute 路书', text: cityOf(post, parseContent(post.content)), url });
      } else {
        setToast(await copyLink(url) ? '分享链接已复制' : '当前浏览器不支持自动复制，请手动复制地址');
      }
    } catch (error) {
      if ((error as { name?: string })?.name !== 'AbortError') setToast('分享暂时不可用，请稍后重试');
    }
  };

  const routeShareCount = useMemo(() => posts.filter((post) => nodesOf(parseContent(stringValue(post.content))).length > 0).length, [posts]);
  const interactionCount = useMemo(() => posts.reduce((sum, post) => sum + (post.likes || 0) + (post.comments || 0) + (post.favorites || 0), 0), [posts]);

  return (
    <main className="community-page min-h-screen w-full bg-[#f4f7fb] font-sans text-slate-900">
      <header className="portal-header border-b border-slate-200 bg-white/95 backdrop-blur"><div className="portal-container mx-auto flex w-full max-w-[1180px] items-center justify-between gap-4 px-4 py-3 sm:px-8"><button type="button" onClick={onBack} className="inline-flex min-h-10 items-center gap-2 rounded-lg px-2.5 text-sm font-bold text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"><ArrowLeft className="h-4 w-4" />返回大厅</button><button type="button" onClick={() => setComposerOpen(true)} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-orange-500 px-3.5 text-xs font-bold text-white shadow-sm transition hover:bg-orange-600"><Plus className="h-4 w-4" />发布路书</button></div></header>

      <div className="mx-auto w-full max-w-[1180px] px-4 py-6 sm:px-8 sm:py-8">
        <section className="community-hero mb-6 flex flex-col justify-between gap-5 md:flex-row md:items-end"><div><p className="community-kicker">Shared intelligence</p><h1 className="mt-1 text-2xl font-black tracking-tight text-slate-900 sm:text-3xl">同行者社区</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">发现真实走过的路线，把别人的经验带入你的下一次协同规划。</p></div><div className="community-hero-metrics"><div><strong>{routeShareCount}</strong><span>结构化路书</span></div><div><strong>{interactionCount}</strong><span>累计互动</span></div><div className="community-live"><span className="h-2 w-2 bg-emerald-500" />实时更新</div></div></section>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px] lg:items-start">
          <section aria-labelledby="community-feed-heading"><h2 id="community-feed-heading" className="sr-only">社区分享列表</h2><div className={`${panel} p-3 sm:p-4`}><div className="flex flex-col gap-3 md:flex-row md:items-center"><label className="relative min-w-0 flex-1"><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" /><input aria-label="搜索社区内容" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="搜索目的地、攻略、标签或作者" className="h-11 w-full rounded-lg border border-slate-200 bg-slate-50 pl-9 pr-3 text-sm font-medium outline-none transition focus:border-orange-400 focus:bg-white focus:ring-2 focus:ring-orange-100" /></label><div className="flex items-center gap-1 overflow-x-auto border border-slate-200 bg-slate-50 p-1"><span className="px-2 text-slate-400"><Flame className="h-3.5 w-3.5" /></span>{([['recommended', '推荐'], ['latest', '最新'], ['hot', '热度']] as const).map(([value, label]) => <button type="button" key={value} onClick={() => setSortMode(value)} className={`min-h-9 shrink-0 whitespace-nowrap px-3 text-xs font-bold transition ${sortMode === value ? 'bg-orange-500 text-white' : 'text-slate-500 hover:bg-white hover:text-slate-900'}`}>{label}</button>)}</div></div><div className="mt-3 flex items-center gap-1 overflow-x-auto border-t border-slate-100 pt-3">{([['discover', '发现'], ['mine', '我的分享'], ['saved', '我的收藏']] as const).map(([value, label]) => <button type="button" key={value} onClick={() => setViewMode(value)} className={`min-h-9 shrink-0 px-3 text-xs font-bold transition ${viewMode === value ? 'border-b-2 border-orange-500 text-orange-600' : 'text-slate-500 hover:text-slate-900'}`}>{label}</button>)}<span className="ml-auto whitespace-nowrap text-[11px] font-semibold text-slate-400">显示 {visiblePosts.length} 条</span></div></div>

          {hotTags.length > 0 && <div className="mt-4 flex items-center gap-2 overflow-x-auto pb-1"><Hash className="h-4 w-4 shrink-0 text-orange-500" />{hotTags.slice(0, 12).map((item) => <button type="button" key={item.tag} onClick={() => selectTag(item.tag)} className={`inline-flex min-h-8 shrink-0 items-center gap-1 border px-2.5 text-xs font-bold transition ${activeTag === item.tag ? 'border-orange-500 bg-orange-500 text-white' : 'border-slate-200 bg-white text-slate-600 hover:border-orange-300 hover:text-orange-600'}`}><Tag className="h-3 w-3" />{item.tag}<span className="text-[10px] opacity-60">{item.count}</span></button>)}</div>}

          <div className="mt-4">{loading ? <div className={`${panel} flex items-center justify-center gap-2 py-20 text-xs font-bold text-slate-400`}><RefreshCw className="h-5 w-5 animate-spin text-orange-500" />正在加载同行路线…</div> : listError ? <ErrorState message={listError} onRetry={() => void loadPosts()} /> : visiblePosts.length === 0 ? <div className={`${panel} border-dashed px-5 py-16 text-center`}><Route className="mx-auto h-10 w-10 text-slate-300" /><p className="mt-4 text-sm font-black text-slate-800">{viewMode === 'saved' ? '还没有收藏路线' : searchQuery || activeTag ? '没有匹配的分享' : '社区正在等待第一位分享者'}</p><p className="mt-1 text-xs leading-5 text-slate-500">{viewMode === 'saved' ? '收藏一条路线，它会出现在这里，方便下次直接采用。' : '调整筛选条件，或发布你的第一本路书。'}</p><button type="button" onClick={() => setComposerOpen(true)} className="mt-5 inline-flex min-h-10 items-center gap-2 bg-orange-500 px-4 text-xs font-bold text-white hover:bg-orange-600"><Plus className="h-4 w-4" />发布分享</button></div> : <div className="space-y-3">{visiblePosts.map((post) => { const parsed = parseContent(stringValue(post.content)); const nodes = nodesOf(parsed); const city = cityOf(post, parsed); const routeIsOpen = routeOpen === post.id; const commentsIsOpen = commentsOpen === post.id; const comments = commentsMap[post.id] || []; const topComments = comments.filter((comment) => !comment.parent_id); const isSaved = favorited.has(post.id); const budget = parsed?.budget ?? parsed?.total_budget; return <article key={post.id} className={`${panel} p-4 sm:p-5`}><div className="flex items-start gap-3"><Avatar src={avatarOf(post)} name={post.author || '旅行者'} className="h-10 w-10 shrink-0 rounded-xl border border-slate-200" /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-black text-slate-900">{post.author || '旅行者'}</span>{city && <span className="inline-flex items-center gap-1 bg-orange-50 px-2 py-1 text-[10px] font-bold text-orange-700"><MapPin className="h-3 w-3" />{city}</span>}<span className="ml-auto inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400"><Clock3 className="h-3.5 w-3.5" />{postDate(post.created_at)}</span></div><h3 className="mt-2 text-base font-black leading-6 text-slate-900">{post.title || '分享了一条旅行路线'}</h3><p className="mt-1 line-clamp-3 text-sm leading-6 text-slate-600">{parsed?.summary || (nodes.length ? `已整理 ${nodes.length} 个行程节点${city ? `，目的地为${city}` : ''}。` : stringValue(post.content, '这位旅行者还没有留下文字说明。'))}</p><div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] font-bold"><span className="inline-flex items-center gap-1 border border-emerald-200 bg-emerald-50 px-2 py-1 text-emerald-700"><ShieldCheck className="h-3.5 w-3.5" />社区共创</span>{nodes.length > 0 && <span className="border border-slate-200 bg-slate-50 px-2 py-1 text-slate-500">{Array.isArray(parsed?.days) && parsed.days.length ? parsed.days.length : 1} 天 · {nodes.length} 节点</span>}{typeof budget === 'number' && <span className="border border-slate-200 bg-slate-50 px-2 py-1 text-slate-500">预算约 ¥{budget}</span>}{(post.likes + (post.favorites || 0)) >= 5 && <span className="inline-flex items-center gap-1 text-orange-600"><Sparkles className="h-3.5 w-3.5" />高采用潜力</span>}</div>{post.tags && post.tags.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{post.tags.slice(0, 8).map((tag) => <button type="button" key={tag} onClick={() => selectTag(tag)} className="inline-flex items-center gap-1 text-[10px] font-bold text-slate-400 transition hover:text-orange-600"><Tag className="h-3 w-3" />{tag}</button>)}</div>}</div></div>
              {nodes.length > 0 && <div className="mt-4"><button type="button" onClick={() => setRouteOpen(routeIsOpen ? null : post.id)} className="inline-flex min-h-9 items-center gap-1.5 border border-orange-200 bg-orange-50 px-3 text-xs font-bold text-orange-700 transition hover:bg-orange-100">{routeIsOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}{routeIsOpen ? '收起路线节点' : `查看 ${nodes.length} 个路线节点`}</button>{routeIsOpen && <div className="mt-3 border-l-2 border-orange-200 pl-4">{nodes.map((node, index) => <div key={`${post.id}-${index}`} className="relative pb-4 last:pb-0"><span className="absolute -left-[25px] top-0 flex h-5 w-5 items-center justify-center rounded-full border-2 border-white bg-orange-100 text-[10px] font-black text-orange-700 ring-1 ring-orange-200">{index + 1}</span><div className="flex flex-wrap items-baseline gap-x-2 gap-y-1"><span className="text-sm font-bold text-slate-800">{stringValue(node?.name || node?.location, '未命名地点')}</span>{node?.time && <span className="text-[11px] font-semibold text-slate-400">{stringValue(node.time).split('|').pop()}</span>}{node?.cost && <span className="text-[11px] font-bold text-emerald-600">{stringValue(node.cost)}</span>}</div>{node?.desc && <p className="mt-1 text-xs leading-5 text-slate-500">{stringValue(node.desc)}</p>}</div>)}</div>}</div>}
              <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-3"><button type="button" onClick={() => void toggleLike(post)} disabled={likeBusy === post.id} className="inline-flex min-h-9 items-center gap-1.5 px-2 text-xs font-bold text-slate-500 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"><Heart className="h-4 w-4" />{post.likes || 0}</button><button type="button" onClick={() => void toggleFavorite(post)} disabled={favoriteBusy === post.id} className={`inline-flex min-h-9 items-center gap-1.5 px-2 text-xs font-bold transition hover:bg-orange-50 disabled:opacity-50 ${isSaved ? 'text-orange-600' : 'text-slate-500 hover:text-orange-600'}`}><Bookmark className={`h-4 w-4 ${isSaved ? 'fill-orange-500' : ''}`} />{post.favorites || 0}</button><button type="button" onClick={() => toggleComments(post.id)} className="inline-flex min-h-9 items-center gap-1.5 px-2 text-xs font-bold text-slate-500 transition hover:bg-sky-50 hover:text-sky-600"><MessageCircle className="h-4 w-4" />{post.comments || 0}</button><button type="button" onClick={() => void sharePost(post)} className="inline-flex min-h-9 items-center gap-1.5 px-2 text-xs font-bold text-slate-500 transition hover:bg-sky-50 hover:text-sky-600"><Share2 className="h-4 w-4" />分享</button><div className="ml-auto flex flex-wrap items-center gap-1.5"><button type="button" onClick={() => void adopt(post, parsed)} disabled={!nodes.length} className="inline-flex min-h-9 items-center gap-1.5 bg-orange-500 px-3 text-xs font-bold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"><Copy className="h-3.5 w-3.5" />采用路线</button>{nodes.length > 0 && <><button type="button" onClick={() => exportPost(post, 'json')} className="inline-flex h-9 w-9 items-center justify-center text-slate-400 transition hover:bg-slate-100 hover:text-orange-600" title="导出 JSON" aria-label="导出 JSON"><Download className="h-4 w-4" /></button><button type="button" onClick={() => exportPost(post, 'markdown')} className="inline-flex h-9 w-9 items-center justify-center text-slate-400 transition hover:bg-slate-100 hover:text-orange-600" title="导出旅行日记" aria-label="导出旅行日记"><BookOpen className="h-4 w-4" /></button></>}</div></div>
              {commentsIsOpen && <div className="mt-3 border-t border-slate-100 pt-3"><div className="mb-3 space-y-3">{topComments.length === 0 ? <p className="text-xs text-slate-400">还没有评论，留下你的第一条建议。</p> : topComments.map((comment) => <div key={comment.id} className="flex items-start gap-2"><Avatar src={avatarOf({ author_avatar_url: comment.author_avatar_url, author_avatar: comment.author_avatar, author: comment.author })} name={comment.author} className="h-7 w-7 shrink-0 rounded-lg" /><div className="min-w-0 flex-1"><p className="text-xs leading-5 text-slate-700"><strong>{comment.author}</strong><span className="ml-1">{comment.content}</span></p><button type="button" onClick={() => { setReplyingTo(comment); setCommentText(`@${comment.author} `); }} className="text-[10px] font-bold text-slate-400 hover:text-orange-600">回复</button>{comments.filter((reply) => reply.parent_id === comment.id).map((reply) => <p key={reply.id} className="mt-1 border-l-2 border-slate-200 pl-2 text-[11px] leading-5 text-slate-500"><strong>{reply.author}</strong> {reply.content}</p>)}</div></div>)}</div><div className="flex items-center gap-2"><input aria-label="评论内容" value={commentText} onChange={(event) => setCommentText(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void sendComment(post.id); }} placeholder={replyingTo ? `回复 ${replyingTo.author}` : '写下你的评论…'} className="h-10 min-w-0 flex-1 border border-slate-200 bg-slate-50 px-3 text-xs outline-none focus:border-orange-400 focus:bg-white" /><button type="button" onClick={() => void sendComment(post.id)} className="inline-flex h-10 w-10 shrink-0 items-center justify-center bg-orange-500 text-white hover:bg-orange-600" title="发送评论" aria-label="发送评论"><Send className="h-4 w-4" /></button></div></div>}
            </article>; })}</div>}</div>
          </section>

          <aside className="space-y-4"><section className={`${panel} p-5`} aria-labelledby="community-guide"><div className="flex items-center justify-between"><h2 id="community-guide" className="text-sm font-black text-slate-900">路线可信度</h2><ShieldCheck className="h-4 w-4 text-emerald-500" /></div><p className="mt-2 text-xs leading-5 text-slate-500">社区路线会标注节点、天数和互动信号，采用前先快速判断是否适合自己。</p><div className="mt-4 space-y-2 text-xs font-bold"><div className="flex items-center justify-between border-b border-slate-100 pb-2"><span className="text-slate-500">结构化节点</span><span className="text-slate-800">可展开核验</span></div><div className="flex items-center justify-between border-b border-slate-100 pb-2"><span className="text-slate-500">互动信号</span><span className="text-slate-800">赞 / 藏 / 评</span></div><div className="flex items-center justify-between"><span className="text-slate-500">采用后</span><span className="text-orange-600">继续个性化编辑</span></div></div></section><section className={`${panel} p-5`} aria-labelledby="community-stats"><h2 id="community-stats" className="text-sm font-black text-slate-900">我的社区数据</h2><div className="mt-3 grid grid-cols-2 gap-px border border-slate-200 bg-slate-200"><div className="bg-slate-50 p-3"><p className="text-[10px] font-bold text-slate-400">我的分享</p><p className="mt-1 text-xl font-black text-slate-900">{posts.filter((post) => post.user_id === currentUser.id).length}</p></div><div className="bg-slate-50 p-3"><p className="text-[10px] font-bold text-slate-400">已收藏</p><p className="mt-1 text-xl font-black text-slate-900">{Array.from(favorited).length}</p></div></div><button type="button" onClick={() => setViewMode('saved')} className="mt-3 inline-flex min-h-10 w-full items-center justify-center gap-2 border border-slate-200 text-xs font-bold text-slate-600 hover:border-orange-300 hover:bg-orange-50 hover:text-orange-700"><Bookmark className="h-4 w-4" />查看收藏路线</button></section></aside>
        </div>
      </div>

      {composerOpen && <div className="fixed inset-0 z-[130] flex items-center justify-center bg-slate-950/45 p-4" role="dialog" aria-modal="true" aria-labelledby="composer-title"><div className="max-h-[calc(100vh-2rem)] w-full max-w-lg overflow-y-auto border border-slate-200 bg-white p-5 shadow-2xl sm:p-6"><div className="flex items-center justify-between"><div><p className="text-[11px] font-black uppercase tracking-wider text-orange-500">Share your route</p><h2 id="composer-title" className="mt-1 text-lg font-black text-slate-900">发布到同行者社区</h2></div><button type="button" onClick={() => setComposerOpen(false)} className="inline-flex h-9 w-9 items-center justify-center text-slate-400 hover:bg-slate-100" title="关闭" aria-label="关闭"><X className="h-5 w-5" /></button></div><div className="mt-5 space-y-4"><label className="block"><span className="mb-1.5 block text-xs font-bold text-slate-600">标题</span><input value={title} maxLength={80} onChange={(event) => setTitle(event.target.value)} placeholder="例如：洛阳老城慢游 2 日路书" className="h-11 w-full border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-orange-400 focus:bg-white" /></label><label className="block"><span className="mb-1.5 block text-xs font-bold text-slate-600">目的地</span><input value={city} maxLength={40} onChange={(event) => setCity(event.target.value)} placeholder="例如：洛阳" className="h-11 w-full border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-orange-400 focus:bg-white" /></label><label className="block"><span className="mb-1.5 block text-xs font-bold text-slate-600">内容或路书 JSON</span><textarea value={content} maxLength={20000} onChange={(event) => setContent(event.target.value)} rows={6} placeholder="分享你的体验，或粘贴规划工作区导出的路书 JSON" className="w-full resize-y border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm leading-5 outline-none focus:border-orange-400 focus:bg-white" /></label><label className="block"><span className="mb-1.5 block text-xs font-bold text-slate-600">标签</span><input value={composerTags} maxLength={120} onChange={(event) => setComposerTags(event.target.value)} placeholder="亲子 美食 自驾（空格分隔）" className="h-11 w-full border border-slate-200 bg-slate-50 px-3 text-sm outline-none focus:border-orange-400 focus:bg-white" /></label><div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"><button type="button" onClick={() => setComposerOpen(false)} className="min-h-11 px-4 text-sm font-bold text-slate-500 hover:bg-slate-100">取消</button><button type="button" onClick={() => void publish()} disabled={publishing || (!title.trim() && !content.trim())} className="inline-flex min-h-11 items-center justify-center gap-2 bg-orange-500 px-5 text-sm font-bold text-white hover:bg-orange-600 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400">{publishing && <RefreshCw className="h-4 w-4 animate-spin" />}发布分享</button></div></div></div></div>}
      {toast && <div className="fixed bottom-5 left-1/2 z-[200] flex max-w-[calc(100vw-2rem)] -translate-x-1/2 items-center gap-2 border border-slate-700 bg-slate-900 px-4 py-3 text-xs font-bold text-white shadow-xl" role="status"><Check className="h-4 w-4 shrink-0 text-emerald-400" /><span className="truncate">{toast}</span></div>}
    </main>
  );
}
