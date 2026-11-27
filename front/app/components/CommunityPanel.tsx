'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowDown,
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Bookmark,
  Check,
  ChevronDown,
  Clock3,
  Download,
  Feather,
  Heart,
  MapPin,
  MessageCircle,
  Plus,
  RefreshCw,
  Search,
  Send,
  Share2,
  ShieldCheck,
  X,
} from 'lucide-react';
import { apiJson, assetUrl } from '../lib/apiClient';
import { generatedAvatarUrl } from '../lib/avatarArt';
import { trackPlanningEvent } from '../lib/planningEvents';
import { buildDiaryMarkdown } from '../lib/tripExport';
import { useFocusTrap } from '../lib/useFocusTrap';
import type { CommunityComment, CommunityPostItem, UserProfile } from '../types';
import JourneySeasonSelect from './JourneySeasonSelect';
import OmniLogo from './OmniLogo';
import WebGuide from './WebGuide';
import { useJourneySeason } from './JourneySeasonProvider';

type ViewMode = 'discover' | 'mine' | 'saved';
type SortMode = 'recommended' | 'latest' | 'hot';
type PostsData = { posts?: CommunityPostItem[] };
type TagsData = { tags?: Array<{ tag: string; count: number }> };
type ParsedContent = ReturnType<typeof parseContent>;

/* ------------------------------------------------------------------ */
/* Copy & season ------------------------------------------------------- */
/* ------------------------------------------------------------------ */

const COMMUNITY_META = {
  spring: {
    eyebrow: 'FIELD NOTES · SPRING / VOL.01',
    motto: '把路上的春天，分享给彼此。',
    desc: '看看真实走过的路线，或将它改编成你的下一程。',
  },
  summer: {
    eyebrow: 'FIELD NOTES · SUMMER / VOL.02',
    motto: '把山海的风，写进同一段路。',
    desc: '看看真实走过的路线，或将它改编成你的下一程。',
  },
  autumn: {
    eyebrow: 'FIELD NOTES · AUTUMN / VOL.03',
    motto: '把层林的秋色，留给同行的人。',
    desc: '看看真实走过的路线，或将它改编成你的下一程。',
  },
  winter: {
    eyebrow: 'FIELD NOTES · WINTER / VOL.04',
    motto: '把雪落的暖意，寄给下一次出发。',
    desc: '看看真实走过的路线，或将它改编成你的下一程。',
  },
} as const;

const TRAVELER_TITLES = {
  spring: '花间漫游者',
  summer: '山海拾风人',
  autumn: '拾光远行者',
  winter: '寻雪旅人',
} as const;

const COVER_PATHS = [
  'M8 66C26 62 22 28 44 32S72 68 94 46 118 22 134 20',
  'M8 72C30 72 20 44 46 46S70 62 90 48 108 30 134 26',
  'M8 60C28 54 38 68 58 48S76 22 98 32 116 42 134 22',
] as const;

/* ------------------------------------------------------------------ */
/* Small helpers ------------------------------------------------------- */
/* ------------------------------------------------------------------ */

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

function nodesOf(parsed: ParsedContent) {
  return Array.isArray(parsed?.routes) ? parsed.routes.filter(Boolean) : [];
}

function cityOf(post: CommunityPostItem, parsed: ParsedContent) {
  return stringValue(post.dest_city).trim() || stringValue(parsed?.city).trim();
}

function daysOf(parsed: ParsedContent, nodes: any[]) {
  if (Array.isArray(parsed?.days) && parsed.days.length) return parsed.days.length;
  return nodes.length ? 1 : 0;
}

function postDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '刚刚' : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function postFullDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '刚刚发布' : date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
}

function plainText(content: string) {
  if (parseContent(content)) return '';
  return content
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[#>*_`~-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// 历史数据里的摘要常是「已为您匹配【四川】3 天…协同行程」这类机器口吻。
// 检测到就退回成事实行，宁可少说，也别让人一眼看出是机器写的。
const MACHINE_COPY = /已为您|为您匹配|协同行程|协同路线|智能规划|多智能体|画像|【|】|深度自然\+/;

function narrativeOf(post: CommunityPostItem, parsed: ParsedContent) {
  const raw = stringValue(parsed?.summary).trim();
  if (raw && !MACHINE_COPY.test(raw)) return raw;
  const plain = plainText(stringValue(post.content));
  if (plain) return plain;
  return '没有留下配图与说明，可从路线节点了解这段行程。';
}

function drawerNarrative(post: CommunityPostItem, parsed: ParsedContent) {
  const raw = stringValue(parsed?.summary).trim();
  if (raw && !MACHINE_COPY.test(raw)) return raw;
  const plain = plainText(stringValue(post.content));
  if (plain) return plain.length > 900 ? `${plain.slice(0, 900)}…` : plain;
  return '这篇手记没有留下文字导语，你可以从下面的路线节点与旅友留言里，读到这趟行程的质地。';
}

function titleOf(post: CommunityPostItem, city: string) {
  const title = stringValue(post.title).trim();
  return title || `${city || '远方'} 的旅行手记`;
}

function avatarOf(post: Pick<CommunityPostItem, 'author_avatar_url' | 'author_avatar' | 'author'>) {
  if (post.author_avatar_url) return assetUrl(post.author_avatar_url);
  return generatedAvatarUrl(post.author_avatar || post.author || 'traveler', post.author);
}

function userAvatar(user: UserProfile) {
  return user.avatarUrl ? assetUrl(user.avatarUrl) : generatedAvatarUrl(user.avatarSeed || user.username, user.nickname);
}

function avatarOfComment(comment: CommunityComment) {
  return avatarOf({
    author_avatar_url: comment.author_avatar_url,
    author_avatar: comment.author_avatar,
    author: comment.author,
  });
}

function editionNo(index: number) {
  return String(index + 1).padStart(2, '0');
}

function nodeName(node: any, index: number) {
  return stringValue(node?.name || node?.location, `第 ${index + 1} 站`);
}

function nodeDesc(node: any) {
  const desc = stringValue(node?.desc || node?.description || node?.note).trim();
  return desc;
}

function nodeTime(node: any) {
  const time = stringValue(node?.time || node?.start_time).trim();
  return time ? time.split('|').pop() || time : '';
}

function factsOf(parsed: ParsedContent, nodes: any[]) {
  const days = daysOf(parsed, nodes);
  return [days ? `${days} 天` : '', nodes.length ? `${nodes.length} 个停留点` : ''].filter(Boolean);
}

function coverSpec(parsed: ParsedContent, nodes: any[], post: CommunityPostItem) {
  const days = daysOf(parsed, nodes);
  if (!nodes.length) return `FIELD NOTE · ${postDate(post.created_at)}`;
  return `${days || 1} DAY${days > 1 ? 'S' : ''} · ${nodes.length} STOP${nodes.length > 1 ? 'S' : ''}`;
}

/* ------------------------------------------------------------------ */
/* Primitives ---------------------------------------------------------- */
/* ------------------------------------------------------------------ */

function Avatar({ src, name, className }: { src: string; name: string; className: string }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => { setFailed(false); setLoaded(false); }, [src]);
  return (
    <div className={`relative flex items-center justify-center overflow-hidden bg-paper-hover ${className}`}>
      <span aria-hidden="true" className={`text-sm font-bold text-ink-3 transition-opacity ${loaded ? 'opacity-0' : 'opacity-100'}`}>{name.trim().slice(0, 1) || '旅'}</span>
      {!failed && <img src={src} alt="" className={`absolute inset-0 z-[1] h-full w-full object-cover transition-opacity duration-300 ${loaded ? 'opacity-100' : 'opacity-0'}`} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />}
    </div>
  );
}

function TravelStamp() {
  return (
    <div className="cj-stamp" aria-hidden="true">
      <svg viewBox="0 0 120 120" fill="none">
        <circle cx="60" cy="60" r="55" stroke="currentColor" strokeWidth="1.2" opacity=".55" />
        <circle cx="60" cy="60" r="46" stroke="currentColor" strokeWidth=".8" opacity=".3" />
        <path id="cj-stamp-arc" d="M17 60a43 43 0 1 1 86 0a43 43 0 1 1-86 0" />
        <text fill="currentColor" fontSize="7" letterSpacing="1.4" opacity=".8">
          <textPath href="#cj-stamp-arc" startOffset="50%" textAnchor="middle">OMNIROUTE COMMUNITY · SHARE THE WAY</textPath>
        </text>
        <path d="M60 24v5M60 91v5M24 60h5M91 60h5" stroke="currentColor" strokeWidth="1" opacity=".4" />
      </svg>
      <span className="cj-stamp-center">同行<small>GO TOGETHER</small></span>
    </div>
  );
}

function HeroRoute() {
  const path = 'M20 172C92 178 52 60 132 82S238 178 282 112 356 28 414 46';
  return (
    <div className="cj-hero-route" aria-hidden="true">
      <p className="cj-route-caption">THE WAY WE SHARE / 同行轨迹</p>
      <svg className="cj-route-svg" viewBox="0 0 430 200" fill="none">
        <path d="M4 178C96 178 62 92 148 108S252 190 316 116 384 42 430 38" stroke="currentColor" strokeWidth="1" opacity=".2" />
        <path className="cj-route-line" d={path} stroke="currentColor" strokeWidth="1.6" strokeDasharray="5 9" />
        <circle cx="20" cy="172" r="5" fill="currentColor" opacity=".85" />
        <circle cx="132" cy="82" r="4" fill="currentColor" opacity=".7" />
        <circle cx="282" cy="112" r="3.4" fill="currentColor" opacity=".55" />
        <circle cx="414" cy="46" r="10" stroke="currentColor" strokeWidth="1.2" opacity=".8" />
        <circle cx="414" cy="46" r="3" fill="currentColor" />
        <circle className="cj-traveler" r="3.2" fill="#fff8ea" style={{ offsetPath: `path('${path}')` }} />
      </svg>
      <span className="cj-route-start">沿途手记</span>
      <span className="cj-route-end">共赴下一程</span>
    </div>
  );
}

function NodeTimeline({ nodes, className = '' }: { nodes: any[]; className?: string }) {
  return (
    <div className={`cj-node-list ${className}`.trim()}>
      {nodes.map((node, index) => {
        const desc = nodeDesc(node);
        const time = nodeTime(node);
        return (
          <div className="cj-node" key={`${index}-${nodeName(node, index)}`}>
            <span className="cj-node-dot" aria-hidden="true" />
            <span className="cj-node-no" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <div className="cj-node-head">
              <span className="cj-node-name">{nodeName(node, index)}</span>
              {time && <span className="cj-node-time">{time}</span>}
            </div>
            {desc && <p className="cj-node-desc">{desc}</p>}
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Route card ---------------------------------------------------------- */
/* ------------------------------------------------------------------ */

function RouteCard({
  post,
  index,
  featured,
  nodes,
  parsed,
  city,
  saved,
  routeOpen,
  likeBusy,
  favoriteBusy,
  onOpen,
  onLike,
  onFavorite,
  onShare,
  onAdopt,
  onExport,
  onTag,
  onToggleRoute,
}: {
  post: CommunityPostItem;
  index: number;
  featured: boolean;
  nodes: any[];
  parsed: ParsedContent;
  city: string;
  saved: boolean;
  routeOpen: boolean;
  likeBusy: boolean;
  favoriteBusy: boolean;
  onOpen: () => void;
  onLike: () => void;
  onFavorite: () => void;
  onShare: () => void;
  onAdopt: () => void;
  onExport: (format: 'json' | 'markdown') => void;
  onTag: (tag: string) => void;
  onToggleRoute: () => void;
}) {
  const title = titleOf(post, city);
  const summary = narrativeOf(post, parsed);
  const facts = factsOf(parsed, nodes);
  const coverPath = COVER_PATHS[index % COVER_PATHS.length];
  const preview = featured && !routeOpen ? nodes.slice(0, 3) : nodes;

  return (
    <article className={`cj-card${featured ? ' is-featured' : ''}`}>
      <span className="cj-card-band" aria-hidden="true" />
      <button
        type="button"
        className={`cj-bookplate${featured ? ' is-featured' : ''}`}
        onClick={onOpen}
        aria-label={`阅读路书：${title}`}
      >
        <span className="cj-bookplate-top"><span>FIELD NOTE</span><span>/ {editionNo(index)}</span></span>
        <span className="cj-bookplate-center">
          <span className="cj-bookplate-route" aria-hidden="true">
            <svg viewBox="0 0 142 84" fill="none">
              <path d={coverPath} stroke="currentColor" strokeWidth="1" strokeDasharray="4 6" />
              <circle cx="8" cy={index % 3 === 2 ? 60 : index % 3 === 1 ? 72 : 66} r="3" fill="currentColor" opacity=".8" />
              <circle cx="134" cy={index % 3 === 2 ? 22 : index % 3 === 1 ? 26 : 20} r="6" stroke="currentColor" strokeWidth="1" opacity=".75" />
              <circle cx="134" cy={index % 3 === 2 ? 22 : index % 3 === 1 ? 26 : 20} r="2" fill="currentColor" />
            </svg>
          </span>
          <span className="cj-bookplate-city" data-long={Array.from(city || '远方').length > 2}>{city || '远方'}</span>
        </span>
        <span className="cj-bookplate-bottom">
          <span className="cj-bookplate-spec">{coverSpec(parsed, nodes, post)}</span>
          <span className="cj-bookplate-origin">OMNIROUTE · 同行者手记</span>
        </span>
      </button>

      <div className="cj-card-main">
        {featured && <p className="cj-featured-kicker">本期推荐 / EDITOR&apos;S PICK</p>}
        <div className="cj-meta">
          <Avatar src={avatarOf(post)} name={post.author || '旅行者'} className="cj-avatar" />
          <div className="cj-meta-author">
            <strong>{post.author || '旅行者'}</strong>
            {city && <span className="cj-city-chip"><MapPin aria-hidden="true" />{city}</span>}
          </div>
          {nodes.length > 0 && (
            <span className="cj-verified"><ShieldCheck aria-hidden="true" />旅友实走 · 可验证</span>
          )}
          <span className="cj-meta-date"><Clock3 aria-hidden="true" />{postDate(post.created_at)}</span>
        </div>

        <button type="button" className="cj-card-title" onClick={onOpen}>{title}</button>
        {facts.length > 0 && (
          <div className="cj-card-facts">
            {facts.map((fact, factIndex) => (
              <React.Fragment key={fact}>
                {factIndex > 0 && <i aria-hidden="true" />}
                <span>{fact}</span>
              </React.Fragment>
            ))}
            <i aria-hidden="true" />
            <span>{nodes.length ? '旅友实走' : '旅行手记'}</span>
          </div>
        )}
        <p className="cj-card-summary">{summary}</p>

        {post.tags && post.tags.length > 0 && (
          <div className="cj-card-tags">
            {post.tags.slice(0, 4).map((tag) => (
              <button type="button" key={tag} onClick={() => onTag(tag)}>#{tag}</button>
            ))}
          </div>
        )}

        {nodes.length > 0 && (
          <div className="cj-axis-wrap">
            <button
              type="button"
              className="cj-axis"
              onClick={onToggleRoute}
              aria-expanded={routeOpen}
            >
              <span className="cj-axis-pin" aria-hidden="true">01</span>
              <span className="cj-axis-name">{nodeName(nodes[0], 0)}</span>
              <span className="cj-axis-track" aria-hidden="true" />
              <span className="cj-axis-toggle">
                {routeOpen ? '收起节点' : featured ? `展开全部 ${nodes.length} 站` : `查看 ${nodes.length} 个节点`}
                <ChevronDown aria-hidden="true" />
              </span>
            </button>
            {(routeOpen || featured) && (
              <>
                <NodeTimeline nodes={preview} />
                <span className="sr-only">共 {nodes.length} 个路线节点</span>
              </>
            )}
          </div>
        )}

        <div className="cj-card-foot">
          <button type="button" className="cj-action" onClick={onLike} disabled={likeBusy} aria-label={`点赞，当前 ${post.likes || 0} 次`}>
            <Heart aria-hidden="true" />{post.likes || 0}
          </button>
          <button type="button" className="cj-action" onClick={onFavorite} disabled={favoriteBusy} data-active={saved} aria-pressed={saved} aria-label={saved ? '取消收藏' : '收藏路线'}>
            <Bookmark aria-hidden="true" />{post.favorites || 0}
          </button>
          <button type="button" className="cj-action" onClick={onOpen} aria-label="查看评论并阅读全文">
            <MessageCircle aria-hidden="true" />{post.comments || 0}
          </button>
          <button type="button" className="cj-action" onClick={onShare} aria-label="分享这篇手记">
            <Share2 aria-hidden="true" /><span>分享</span>
          </button>
          <div className="cj-foot-right">
            {nodes.length > 0 && (
              <>
                <button type="button" onClick={() => onExport('json')} className="cj-icon-btn" title="导出 JSON" aria-label="导出 JSON">
                  <Download aria-hidden="true" />
                </button>
                <button type="button" onClick={() => onExport('markdown')} className="cj-icon-btn" title="导出旅行日记" aria-label="导出旅行日记">
                  <BookOpen aria-hidden="true" />
                </button>
              </>
            )}
            <button type="button" className="cj-adopt" onClick={onAdopt} disabled={!nodes.length} aria-label="采用这条路线">
              采用路线<ArrowUpRight aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------ */
/* Personal passport & trust ------------------------------------------ */
/* ------------------------------------------------------------------ */

function PassportCard({
  user,
  season,
  myCount,
  savedCount,
  receivedCount,
  stamps,
  onWrite,
  onSaved,
  onMine,
}: {
  user: UserProfile;
  season: keyof typeof TRAVELER_TITLES;
  myCount: number;
  savedCount: number;
  receivedCount: number;
  stamps: Array<{ key: string; season: string; title: string; note: string; lit: boolean }>;
  onWrite: () => void;
  onSaved: () => void;
  onMine: () => void;
}) {
  return (
    <section className="cj-paper-card" aria-labelledby="cj-passport-name">
      <div className="cj-passport-band" aria-hidden="true">
        <div className="cj-passport-contours">
          <svg viewBox="0 0 360 84" fill="none">
            <path d="M-10 70C46 66 58 24 112 30S176 76 224 52 288 18 372 26" stroke="currentColor" strokeWidth=".8" />
            <path d="M-10 78C48 74 64 36 118 40S184 84 232 60 296 28 372 34" stroke="currentColor" strokeWidth=".7" opacity=".7" />
            <path d="M-10 62C44 58 52 16 106 22S170 68 218 44 282 10 372 18" stroke="currentColor" strokeWidth=".6" opacity=".45" />
          </svg>
        </div>
        <span className="cj-passport-edition">TRAVELER FILE / OMNIROUTE</span>
      </div>
      <div className="cj-passport-body">
        <div className="cj-passport-id">
          <Avatar src={userAvatar(user)} name={user.nickname || user.username} className="cj-passport-avatar" />
          <div className="cj-passport-name">
            <h2 id="cj-passport-name">{user.nickname || user.username}</h2>
            <span className="cj-passport-title">{TRAVELER_TITLES[season]}</span>
          </div>
        </div>
        <p className="cj-passport-signature">{user.signature || '还没有写下旅行签名，先把下一段路写进手记。'}</p>

        <div className="cj-passport-stats">
          <button type="button" className="cj-stat" data-clickable="true" onClick={onMine} aria-label="查看我的手记">
            <strong>{myCount}</strong><span>我的手记</span>
          </button>
          <button type="button" className="cj-stat" data-clickable="true" onClick={onSaved} aria-label="查看珍藏路书">
            <strong>{savedCount}</strong><span>珍藏路书</span>
          </button>
          <div className="cj-stat" data-clickable="false">
            <strong>{receivedCount}</strong><span>被收藏</span>
          </div>
        </div>

        <div className="cj-stamp-wall">
          <div className="cj-stamp-wall-head">
            <strong>四季行迹</strong>
            <span>按分享与收藏点亮</span>
          </div>
          <div className="cj-season-stamps">
            {stamps.map((stamp) => (
              <div
                className="cj-season-stamp"
                key={stamp.key}
                data-lit={stamp.lit}
                title={`${stamp.title} · ${stamp.note}${stamp.lit ? '（已点亮）' : '（未点亮）'}`}
              >
                <span className="cj-season-stamp-circle">{stamp.season}</span>
                <small>{stamp.title}</small>
              </div>
            ))}
          </div>
        </div>

        <div className="cj-passport-actions">
          <button type="button" className="cj-passport-primary" onClick={onWrite}>
            <Feather aria-hidden="true" />写一篇新手记
          </button>
          <button type="button" className="cj-passport-secondary" onClick={onSaved}>
            <Bookmark aria-hidden="true" />查看收藏夹
          </button>
        </div>
      </div>
    </section>
  );
}

function TrustCard() {
  return (
    <section className="cj-trust" aria-labelledby="cj-trust-head">
      <div className="cj-trust-head">
        <h2 id="cj-trust-head"><ShieldCheck aria-hidden="true" />路书可信公约</h2>
      </div>
      <div className="cj-trust-item">
        <span className="cj-trust-no">01</span>
        <div><strong>节点可展开核验</strong><p>每一站的时间、停留与贴士都能在卡片内展开查看。</p></div>
      </div>
      <div className="cj-trust-item">
        <span className="cj-trust-no">02</span>
        <div><strong>实走与互动信号</strong><p>「旅友实走」印章与赞、藏、评，都是判断参考的一部分。</p></div>
      </div>
      <div className="cj-trust-item">
        <span className="cj-trust-no">03</span>
        <div><strong>采用后可自由改编</strong><p>一键带入规划大厅，继续改成自己的节奏与预算。</p></div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Reading drawer ------------------------------------------------------ */
/* ------------------------------------------------------------------ */

function SchematicRoute({ nodes }: { nodes: any[] }) {
  const shown = nodes.slice(0, 6);
  const count = shown.length;
  const points = shown.map((_, index) => ({
    x: count === 1 ? 280 : 30 + (500 / (count - 1)) * index,
    y: 46 + Math.sin(index * 1.4) * 14,
    index,
  }));
  return (
    <div className="cj-route-schematic">
      <svg viewBox="0 0 560 92" fill="none" aria-hidden="true">
        <polyline
          points={points.map((point) => `${point.x},${point.y}`).join(' ')}
          stroke="currentColor"
          strokeWidth="1"
          strokeDasharray="4 7"
          opacity=".4"
        />
        {points.map((point) => (
          <g key={point.index}>
            <circle cx={point.x} cy={point.y} r="3.4" fill="currentColor" />
            <circle cx={point.x} cy={point.y} r="7.5" stroke="currentColor" strokeWidth=".7" opacity=".35" />
            <text x={point.x} y={point.y - 12} textAnchor="middle" fontSize="8" fill="currentColor" opacity=".7">
              {String(point.index + 1).padStart(2, '0')}
            </text>
          </g>
        ))}
      </svg>
      <p><span>{nodeName(nodes[0], 0)}</span><span>{nodeName(nodes[nodes.length - 1], nodes.length - 1)}</span></p>
    </div>
  );
}

function RouteDrawer({
  post,
  parsed,
  nodes,
  city,
  comments,
  saved,
  likeBusy,
  favoriteBusy,
  commentText,
  replyingTo,
  onClose,
  onLike,
  onFavorite,
  onShare,
  onAdopt,
  onExport,
  onSendComment,
  onCommentText,
  onReply,
  onTag,
}: {
  post: CommunityPostItem;
  parsed: ParsedContent;
  nodes: any[];
  city: string;
  comments: CommunityComment[];
  saved: boolean;
  likeBusy: boolean;
  favoriteBusy: boolean;
  commentText: string;
  replyingTo: CommunityComment | null;
  onClose: () => void;
  onLike: () => void;
  onFavorite: () => void;
  onShare: () => void;
  onAdopt: () => void;
  onExport: (format: 'json' | 'markdown') => void;
  onSendComment: () => void;
  onCommentText: (value: string) => void;
  onReply: (comment: CommunityComment) => void;
  onTag: (tag: string) => void;
}) {
  const drawerRef = useFocusTrap<HTMLDivElement>(true);
  const topComments = comments.filter((comment) => !comment.parent_id);
  const title = titleOf(post, city);

  return (
    <div className="cj-drawer-layer">
      <button type="button" className="cj-backdrop" onClick={onClose} aria-label="关闭路书详情" />
      <aside className="cj-drawer" role="dialog" aria-modal="true" aria-labelledby="cj-drawer-title" ref={drawerRef}>
        <div className="cj-drawer-cover">
          <div className="cj-drawer-cover-art" aria-hidden="true" />
          <button type="button" className="cj-drawer-close" onClick={onClose} aria-label="关闭路书详情"><X aria-hidden="true" /></button>
          <p>ROUTE FILE / 路书详情</p>
          <h2 id="cj-drawer-title">{title}</h2>
          <div className="cj-drawer-cover-meta">
            <Avatar src={avatarOf(post)} name={post.author || '旅行者'} className="cj-avatar" />
            <span>{post.author || '旅行者'}</span>
            {city && <span>· {city}</span>}
            <span>· {postFullDate(post.created_at)}</span>
          </div>
        </div>

        <div className="cj-drawer-body">
          <p className="cj-drawer-intro">{drawerNarrative(post, parsed)}</p>

          <div className="cj-drawer-facts">
            {factsOf(parsed, nodes).map((fact) => (
              <span className="cj-fact" key={fact}>{fact}</span>
            ))}
            {nodes.length > 0
              ? <span className="cj-fact" data-tone="good"><ShieldCheck aria-hidden="true" />旅友实走 · 可验证</span>
              : <span className="cj-fact">旅行手记</span>}
          </div>

          {post.tags && post.tags.length > 0 && (
            <div className="cj-card-tags">
              {post.tags.map((tag) => <button type="button" key={tag} onClick={() => onTag(tag)}>#{tag}</button>)}
            </div>
          )}

          <section className="cj-drawer-section" aria-labelledby="cj-drawer-nodes">
            <div className="cj-section-head">
              <h3 id="cj-drawer-nodes">每日路线节点</h3>
              <small>{nodes.length ? `${nodes.length} STOPS / ${daysOf(parsed, nodes) || 1} DAYS` : 'NO STOPS'}</small>
            </div>
            {nodes.length > 0
              ? <NodeTimeline nodes={nodes} className="cj-drawer-node-list" />
              : <p className="cj-comment-empty">这篇手记没有附带可展开的路线节点。</p>}
          </section>

          {nodes.length > 1 && (
            <section className="cj-drawer-section" aria-labelledby="cj-drawer-map">
              <div className="cj-section-head">
                <h3 id="cj-drawer-map">路线示意</h3>
                <small>SCHEMATIC · 非地理比例</small>
              </div>
              <SchematicRoute nodes={nodes} />
            </section>
          )}

          <section className="cj-comments" aria-labelledby="cj-drawer-comments">
            <div className="cj-section-head">
              <h3 id="cj-drawer-comments">旅友留言</h3>
              <small>{topComments.length} NOTES</small>
            </div>
            <div className="cj-comment-list">
              {topComments.length === 0
                ? <p className="cj-comment-empty">还没有留言。如果你走过同一条路，留一句给下一位旅人。</p>
                : topComments.map((comment) => (
                  <div className="cj-comment" key={comment.id}>
                    <Avatar src={avatarOfComment(comment)} name={comment.author} className="cj-comment-avatar" />
                    <div className="cj-comment-body">
                      <p><strong>{comment.author}</strong>{comment.content}</p>
                      <button type="button" className="cj-comment-tools" onClick={() => onReply(comment)}>回复</button>
                      {comments.filter((reply) => reply.parent_id === comment.id).map((reply) => (
                        <p className="cj-comment-reply" key={reply.id}><strong>{reply.author}</strong> {reply.content}</p>
                      ))}
                    </div>
                  </div>
                ))}
            </div>
            <div className="cj-comment-form">
              <input
                aria-label="留言内容"
                value={commentText}
                onChange={(event) => onCommentText(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') onSendComment(); }}
                placeholder={replyingTo ? `回复 ${replyingTo.author}` : '写下你的留言…'}
              />
              <button type="button" onClick={onSendComment} title="发送留言" aria-label="发送留言"><Send aria-hidden="true" /></button>
            </div>
          </section>
        </div>

        <div className="cj-drawer-foot">
          <button type="button" className="cj-action" onClick={onLike} disabled={likeBusy}><Heart aria-hidden="true" />{post.likes || 0}</button>
          <button type="button" className="cj-action" onClick={onFavorite} disabled={favoriteBusy} data-active={saved} aria-pressed={saved}><Bookmark aria-hidden="true" />{post.favorites || 0}</button>
          <button type="button" className="cj-action" onClick={onShare}><Share2 aria-hidden="true" />分享</button>
          <div className="cj-foot-right">
            {nodes.length > 0 && (
              <>
                <button type="button" onClick={() => onExport('json')} className="cj-icon-btn" title="导出 JSON" aria-label="导出 JSON"><Download aria-hidden="true" /></button>
                <button type="button" onClick={() => onExport('markdown')} className="cj-icon-btn" title="导出旅行日记" aria-label="导出旅行日记"><BookOpen aria-hidden="true" /></button>
              </>
            )}
            <button type="button" className="cj-adopt" onClick={onAdopt} disabled={!nodes.length}>采用并改编<ArrowUpRight aria-hidden="true" /></button>
          </div>
        </div>
      </aside>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Composer ------------------------------------------------------------ */
/* ------------------------------------------------------------------ */

function ComposerModal({
  title,
  city,
  content,
  tags,
  publishing,
  onTitle,
  onCity,
  onContent,
  onTags,
  onClose,
  onSubmit,
}: {
  title: string;
  city: string;
  content: string;
  tags: string;
  publishing: boolean;
  onTitle: (value: string) => void;
  onCity: (value: string) => void;
  onContent: (value: string) => void;
  onTags: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  const modalRef = useFocusTrap<HTMLDivElement>(true);
  return (
    <div className="cj-modal-layer">
      <button type="button" className="cj-modal-backdrop" onClick={onClose} aria-label="关闭发布弹窗" />
      <div className="cj-modal" role="dialog" aria-modal="true" aria-labelledby="cj-composer-title" ref={modalRef}>
        <div className="cj-modal-head">
          <div>
            <p>NEW FIELD NOTE / 新手记</p>
            <h2 id="cj-composer-title">发布到同行者社区</h2>
            <p className="cj-modal-note">写下真实走过的路线，或直接粘贴规划工作区导出的路书 JSON。</p>
          </div>
          <button type="button" className="cj-modal-close" onClick={onClose} aria-label="关闭"><X aria-hidden="true" /></button>
        </div>
        <form className="cj-form" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
          <label className="cj-field">
            <span>标题 <em>必填</em></span>
            <input value={title} maxLength={80} onChange={(event) => onTitle(event.target.value)} placeholder="例如：成都老城 1 日漫游手记" />
          </label>
          <label className="cj-field">
            <span>目的地</span>
            <input value={city} maxLength={40} onChange={(event) => onCity(event.target.value)} placeholder="例如：成都" />
          </label>
          <label className="cj-field">
            <span>手记正文 / 路书 JSON</span>
            <textarea value={content} maxLength={20000} rows={6} onChange={(event) => onContent(event.target.value)} placeholder="分享路上的见闻，或粘贴导出的路书 JSON" />
          </label>
          <label className="cj-field">
            <span>标签 <em>空格分隔</em></span>
            <input value={tags} maxLength={120} onChange={(event) => onTags(event.target.value)} placeholder="亲子 美食 自驾" />
          </label>
          <div className="cj-form-actions">
            <button type="button" className="cj-form-cancel" onClick={onClose}>取消</button>
            <button type="submit" className="cj-form-submit" disabled={publishing || (!title.trim() && !content.trim())}>
              {publishing && <RefreshCw className="animate-spin" aria-hidden="true" />}
              发布手记
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page ---------------------------------------------------------------- */
/* ------------------------------------------------------------------ */

export default function CommunityPanel({
  currentUser,
  onBack,
  onAdoptPlanning,
}: {
  currentUser: UserProfile;
  onBack: () => void;
  onAdoptPlanning?: (city: string) => void;
}) {
  const { season } = useJourneySeason();
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
  const [activePostId, setActivePostId] = useState<string | null>(null);
  const [commentsMap, setCommentsMap] = useState<Record<string, CommunityComment[]>>({});
  const [commentText, setCommentText] = useState('');
  const [replyingTo, setReplyingTo] = useState<CommunityComment | null>(null);
  const [likeBusy, setLikeBusy] = useState<string | null>(null);
  const [favoriteBusy, setFavoriteBusy] = useState<string | null>(null);
  const [favorited, setFavorited] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState('');
  const feedRef = useRef<HTMLElement>(null);

  const seasonMeta = COMMUNITY_META[season];

  const loadPosts = useCallback(async () => {
    setLoading(true);
    setListError('');
    try {
      // Always load the full feed. Ownership, saved and tag filters are applied
      // locally so the passport counters stay stable while the feed is filtered.
      const data = await apiJson<PostsData>('/api/community/list');
      const next = Array.isArray(data.posts) ? data.posts : [];
      setPosts(next);
      setFavorited(new Set(next.filter((post) => Boolean(post.favorited)).map((post) => post.id)));
    } catch (error) {
      setListError(error instanceof Error ? error.message : '数据服务暂时不可用');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTags = useCallback(async () => {
    try {
      const data = await apiJson<TagsData>('/api/community/tags');
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

  useEffect(() => {
    if (!activePostId && !composerOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, [activePostId, composerOpen]);

  useEffect(() => {
    if (!activePostId && !composerOpen) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (composerOpen) setComposerOpen(false);
      else setActivePostId(null);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [activePostId, composerOpen]);

  const myPosts = useMemo(() => posts.filter((post) => post.user_id === currentUser.id), [currentUser.id, posts]);
  const myRoutePostCount = useMemo(
    () => myPosts.filter((post) => nodesOf(parseContent(stringValue(post.content))).length > 0).length,
    [myPosts],
  );
  const receivedCount = useMemo(
    () => myPosts.reduce((sum, post) => sum + (post.likes || 0) + (post.comments || 0) + (post.favorites || 0), 0),
    [myPosts],
  );

  const visiblePosts = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase();
    return posts
      .filter((post) => {
        if (viewMode === 'mine' && post.user_id !== currentUser.id) return false;
        if (viewMode === 'saved' && !favorited.has(post.id)) return false;
        if (activeTag && !(post.tags || []).includes(activeTag)) return false;
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
  }, [activeTag, currentUser.id, favorited, posts, searchQuery, sortMode, viewMode]);

  const activePost = useMemo(
    () => posts.find((post) => post.id === activePostId) || null,
    [activePostId, posts],
  );
  const activeParsed = activePost ? parseContent(stringValue(activePost.content)) : null;
  const activeNodes = activePost ? nodesOf(activeParsed) : [];
  const activeCity = activePost ? cityOf(activePost, activeParsed) : '';

  const routeShareCount = useMemo(
    () => posts.filter((post) => nodesOf(parseContent(stringValue(post.content))).length > 0).length,
    [posts],
  );
  const interactionCount = useMemo(
    () => posts.reduce((sum, post) => sum + (post.likes || 0) + (post.comments || 0) + (post.favorites || 0), 0),
    [posts],
  );

  const tagChips = useMemo(() => {
    if (hotTags.length) return hotTags.slice(0, 14);
    const counts = new Map<string, number>();
    posts.forEach((post) => (post.tags || []).forEach((tag) => counts.set(tag, (counts.get(tag) || 0) + 1)));
    return Array.from(counts, ([tag, count]) => ({ tag, count })).slice(0, 14);
  }, [hotTags, posts]);

  const stamps = useMemo(() => [
    { key: 'spring', season: '春', title: '启程', note: '发布第一篇手记', lit: myPosts.length >= 1 },
    { key: 'summer', season: '夏', title: '实走', note: '分享一条带节点的路书', lit: myRoutePostCount >= 1 },
    { key: 'autumn', season: '秋', title: '珍藏', note: '收藏一篇旅友路书', lit: favorited.size >= 1 },
    { key: 'winter', season: '冬', title: '回响', note: '收获一次赞、收藏或评论', lit: receivedCount >= 1 },
  ], [favorited.size, myPosts.length, myRoutePostCount, receivedCount]);

  const loadComments = async (postId: string) => {
    try {
      const data = await apiJson<{ comments?: CommunityComment[] }>(`/api/community/comments?post_id=${encodeURIComponent(postId)}`);
      setCommentsMap((current) => ({ ...current, [postId]: Array.isArray(data.comments) ? data.comments : [] }));
    } catch (error) {
      setToast(error instanceof Error ? error.message : '评论加载失败');
    }
  };

  const openPost = (post: CommunityPostItem) => {
    setActivePostId(post.id);
    setReplyingTo(null);
    setCommentText('');
    if (!commentsMap[post.id]) void loadComments(post.id);
  };

  const closeDrawer = () => {
    setActivePostId(null);
    setReplyingTo(null);
    setCommentText('');
  };

  const scrollToFeed = () => {
    feedRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const selectTag = (tag: string) => {
    setActiveTag((current) => current === tag ? '' : tag);
    setActivePostId(null);
  };

  const publish = async () => {
    if (!title.trim() && !content.trim()) return;
    setPublishing(true);
    try {
      const tags = composerTags.split(/[\s,#，]+/).map((tag) => tag.trim()).filter(Boolean).slice(0, 8);
      await apiJson(`/api/community/post`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, title: title.trim(), content: content.trim(), dest_city: city.trim(), tags }),
      });
      setComposerOpen(false);
      setTitle(''); setCity(''); setContent(''); setComposerTags('');
      setToast('手记已发布');
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
      const data = await apiJson<{ likes?: number }>(`/api/community/like`, {
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
      const data = await apiJson<{ favorites?: number; favorited?: boolean }>(`/api/community/favorite`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ post_id: post.id, user_id: currentUser.id }),
      });
      setFavorited((current) => { const next = new Set(current); if (data.favorited) next.add(post.id); else next.delete(post.id); return next; });
      if (typeof data.favorites === 'number') setPosts((current) => current.map((item) => item.id === post.id ? { ...item, favorites: data.favorites as number, favorited: Boolean(data.favorited) } : item));
      trackPlanningEvent({ event_type: 'plan_favorited', plan_id: post.id, payload: { module: 'community', favorited: Boolean(data.favorited) } });
    } catch (error) {
      setToast(error instanceof Error ? error.message : '收藏失败');
    } finally {
      setFavoriteBusy(null);
    }
  };

  const sendComment = async (postId: string) => {
    const value = commentText.trim();
    if (!value) return;
    try {
      await apiJson(`/api/community/comment`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ post_id: postId, user_id: currentUser.id, content: value, parent_id: replyingTo?.id || '' }),
      });
      setCommentText(''); setReplyingTo(null);
      await loadComments(postId);
      setPosts((current) => current.map((post) => post.id === postId ? { ...post, comments: (post.comments || 0) + 1 } : post));
      setToast('留言已发送');
    } catch (error) {
      setToast(error instanceof Error ? error.message : '留言失败，请稍后重试');
    }
  };

  const adopt = async (post: CommunityPostItem, parsed: ParsedContent) => {
    const city = cityOf(post, parsed);
    const nodes = nodesOf(parsed);
    if (!parsed || !nodes.length) {
      setToast('这篇手记里没有可复用的路线节点');
      return;
    }
    try {
      await apiJson(`/api/user/trip`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: currentUser.id, title: post.title || `${city || '目的地'} 行程`, dest_city: city, content: post.content }),
      });
      setToast('已加入我的路书，正在打开规划工作区');
      onAdoptPlanning?.(city);
    } catch (error) {
      setToast(error instanceof Error ? error.message : '复制路书失败，请稍后重试');
    }
  };

  const exportPost = (post: CommunityPostItem, format: 'json' | 'markdown') => {
    const parsed = parseContent(stringValue(post.content));
    const city = cityOf(post, parsed);
    const safeTitle = (post.title || `${city || '路线'} 路书`).replace(/[\\/:*?"<>|]/g, '-');
    if (format === 'json') {
      fileDownload(`${safeTitle}.json`, JSON.stringify(parsed || { content: post.content }, null, 2), 'application/json;charset=utf-8');
    } else {
      fileDownload(`${safeTitle}.md`, buildDiaryMarkdown(nodesOf(parsed), { city }), 'text/markdown;charset=utf-8');
    }
    setToast(format === 'json' ? '路书数据已导出' : '旅行日记已导出');
  };

  const sharePost = async (post: CommunityPostItem) => {
    const url = `${window.location.origin}${window.location.pathname}?post=${encodeURIComponent(post.id)}`;
    try {
      if (navigator.share) {
        await navigator.share({ title: post.title || 'OmniRoute 路书', text: cityOf(post, parseContent(stringValue(post.content))), url });
      } else {
        setToast(await copyLink(url) ? '分享链接已复制' : '当前浏览器不支持自动复制，请手动复制地址');
      }
    } catch (error) {
      if ((error as { name?: string })?.name !== 'AbortError') setToast('分享暂时不可用，请稍后重试');
    }
  };

  const sparse = visiblePosts.length > 0 && visiblePosts.length <= 2 && !searchQuery.trim() && !activeTag && viewMode === 'discover';

  const empty = viewMode === 'saved'
    ? { eyebrow: 'YOUR ARCHIVE / 珍藏夹', title: '收藏夹还空着。', desc: '在漫游发现里遇到心动的路线，点一下收藏，它就会留在这里。', primary: '去漫游发现', action: () => setViewMode('discover') }
    : viewMode === 'mine'
      ? { eyebrow: 'MY FIELD NOTES / 我的手记', title: '还没有写下自己的手记。', desc: '把走过的路留成一页纸，也让下一位旅人少走一点弯路。', primary: '写第一篇手记', action: () => setComposerOpen(true) }
      : { eyebrow: 'NO MATCH / 暂无匹配', title: '没有匹配的手记。', desc: '换个目的地、标签或作者试试，也可以清空筛选回到全部路书。', primary: '清空筛选', action: () => { setSearchQuery(''); setActiveTag(''); } };

  const showColdStart = !loading && !listError && posts.length === 0 && viewMode === 'discover' && !searchQuery.trim() && !activeTag;

  return (
    <main className="community-page community-journal" data-season={season}>
      <div className="cj-wrap">
        <div className="cj-frame">
          <header className="cj-header">
            <div className="cj-header-left">
              <button type="button" onClick={onBack} className="cj-back"><ArrowLeft aria-hidden="true" />返回大厅</button>
              <span className="cj-header-divider" aria-hidden="true" />
              <div className="cj-brand">
                <OmniLogo className="cj-brand-logo" />
                <div className="cj-brand-text"><strong>OmniRoute</strong><span>同行者社区</span></div>
              </div>
            </div>
            <div className="cj-header-right">
              <div className="cj-guide-slot"><WebGuide startAt="community" /></div>
              <JourneySeasonSelect />
              <button type="button" onClick={() => setComposerOpen(true)} className="cj-publish"><Plus aria-hidden="true" />发布路书</button>
              <div className="cj-header-avatar" title={currentUser.nickname || currentUser.username}>
                <Avatar src={userAvatar(currentUser)} name={currentUser.nickname || currentUser.username} className="h-full w-full" />
              </div>
            </div>
          </header>

          <section className="cj-hero" aria-labelledby="community-heading">
            <div className="cj-hero-art community-cover" aria-hidden="true" />
            <div className="cj-hero-top">
              <div className="cj-hero-copy">
                <p className="cj-eyebrow">{seasonMeta.eyebrow}</p>
                <h1 id="community-heading">同行者社区</h1>
                <p className="cj-hero-motto">{seasonMeta.motto}</p>
                <p className="cj-hero-desc">{seasonMeta.desc}</p>
              </div>
              <HeroRoute />
            </div>
            <div className="cj-hero-bottom">
              <div className="cj-hero-stats">
                {viewMode === 'discover' ? (
                  <>
                    <span><b>{routeShareCount}</b>份实走路书</span>
                    <span className="cj-split" aria-hidden="true" />
                    <span><b>{interactionCount}</b>次旅友互动</span>
                    <span className="cj-split" aria-hidden="true" />
                    <span className="cj-live"><i aria-hidden="true" />持续更新</span>
                  </>
                ) : viewMode === 'mine' ? (
                  <>
                    <span><b>{myPosts.length}</b>篇我的手记</span>
                    <span className="cj-split" aria-hidden="true" />
                    <span><b>{receivedCount}</b>次旅友回响</span>
                    <span className="cj-split" aria-hidden="true" />
                    <span className="cj-live"><i aria-hidden="true" />持续记录</span>
                  </>
                ) : (
                  <>
                    <span><b>{favorited.size}</b>篇珍藏路书</span>
                    <span className="cj-split" aria-hidden="true" />
                    <span><b>{myRoutePostCount}</b>条实走路线</span>
                    <span className="cj-split" aria-hidden="true" />
                    <span className="cj-live"><i aria-hidden="true" />随时回看</span>
                  </>
                )}
              </div>
              <div className="cj-hero-actions">
                <button type="button" className="cj-hero-link" onClick={scrollToFeed}><ArrowDown aria-hidden="true" />浏览精选</button>
                <button type="button" className="cj-hero-publish" onClick={() => setComposerOpen(true)}><Feather aria-hidden="true" />发布路书</button>
              </div>
            </div>
            <TravelStamp />
          </section>

          <section className="cj-controls" aria-label="筛选与排序">
            <div className="cj-controls-top">
              <div className="cj-tabs" role="group" aria-label="社区视角">
                {([['discover', '漫游发现'], ['mine', '我的手记'], ['saved', '珍藏路书']] as const).map(([value, label]) => (
                  <button
                    type="button"
                    key={value}
                    className="cj-tab"
                    data-active={viewMode === value}
                    aria-pressed={viewMode === value}
                    onClick={() => setViewMode(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <label className="cj-search">
                <Search aria-hidden="true" />
                <input
                  data-web-guide="community-search"
                  aria-label="搜索社区内容"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="搜索目的地、路书标题、标签或作者..."
                />
              </label>
              <div className="cj-sort" role="group" aria-label="排序方式">
                {([['recommended', '推荐'], ['latest', '最新'], ['hot', '热度']] as const).map(([value, label]) => (
                  <button
                    type="button"
                    key={value}
                    data-active={sortMode === value}
                    aria-pressed={sortMode === value}
                    onClick={() => setSortMode(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="cj-tagrow">
              <div className="cj-tag-scroll">
                <button type="button" className="cj-tag" data-active={!activeTag} aria-pressed={!activeTag} onClick={() => setActiveTag('')}>
                  全部路书<em>{posts.length}</em>
                </button>
                {tagChips.map((item) => (
                  <button
                    type="button"
                    key={item.tag}
                    className="cj-tag"
                    data-active={activeTag === item.tag}
                    aria-pressed={activeTag === item.tag}
                    onClick={() => setActiveTag((current) => current === item.tag ? '' : item.tag)}
                  >
                    {item.tag}<em>{item.count}</em>
                  </button>
                ))}
              </div>
              <span className="cj-tag-count" role="status">共收录 {visiblePosts.length} 篇手记</span>
            </div>
          </section>

          <div className="cj-body">
            <section className="cj-feed" ref={feedRef} aria-labelledby="community-feed-heading" aria-busy={loading}>
              <h2 id="community-feed-heading" className="sr-only">社区路书列表</h2>

              {loading && (
                <>
                  <div className="cj-skeleton" aria-hidden="true"><div className="cj-skeleton-cover" /><div className="cj-skeleton-main"><div className="cj-skeleton-line" /><div className="cj-skeleton-line" /><div className="cj-skeleton-line" /><div className="cj-skeleton-line" /></div></div>
                  <div className="cj-skeleton" aria-hidden="true"><div className="cj-skeleton-cover" /><div className="cj-skeleton-main"><div className="cj-skeleton-line" /><div className="cj-skeleton-line" /><div className="cj-skeleton-line" /><div className="cj-skeleton-line" /></div></div>
                  <span className="sr-only">正在加载同行者的路书…</span>
                </>
              )}

              {!loading && listError && (
                <div className="cj-error" role="alert">
                  <AlertCircle aria-hidden="true" />
                  <div>
                    <strong>社区暂时无法连接</strong>
                    <p>{listError}</p>
                    <button type="button" onClick={() => void loadPosts()}><RefreshCw aria-hidden="true" /> 重试</button>
                  </div>
                </div>
              )}

              {!loading && !listError && showColdStart && (
                <div className="cj-empty-spread">
                  <div className="cj-empty-plate" aria-hidden="true">
                    <span>远方</span>
                    <small>BLANK PAGE / {seasonMeta.eyebrow.split('·')[1]?.trim() || 'VOL.01'}</small>
                  </div>
                  <div className="cj-empty-copy">
                    <p>THIS SEASON&apos;S FIRST PAGE</p>
                    <h2>这一季的远方，<br />正等待第一份完整手记。</h2>
                    <p>把真实走过的路线留在这里。一张节点清晰的路线，往往比十篇攻略更能帮到下一位旅人。</p>
                    <div className="cj-empty-actions">
                      <button type="button" className="cj-invite-primary" onClick={() => setComposerOpen(true)}><Plus aria-hidden="true" />发布第一篇手记</button>
                      <button type="button" className="cj-invite-secondary" onClick={() => setViewMode('saved')}><Bookmark aria-hidden="true" />先看看珍藏夹</button>
                    </div>
                  </div>
                </div>
              )}

              {!loading && !listError && !showColdStart && visiblePosts.length === 0 && (
                <div className="cj-invite">
                  <p className="cj-invite-eyebrow">{empty.eyebrow}</p>
                  <h3>{empty.title}</h3>
                  <p>{empty.desc}</p>
                  <div className="cj-invite-actions">
                    <button type="button" className="cj-invite-primary" onClick={empty.action}>{empty.primary}<ArrowUpRight aria-hidden="true" /></button>
                  </div>
                </div>
              )}

              {!loading && !listError && visiblePosts.length > 0 && (
                <>
                  {visiblePosts.map((post, index) => {
                    const parsed = parseContent(stringValue(post.content));
                    const nodes = nodesOf(parsed);
                    const city = cityOf(post, parsed);
                    return (
                      <RouteCard
                        key={post.id}
                        post={post}
                        index={index}
                        featured={sparse && index === 0}
                        nodes={nodes}
                        parsed={parsed}
                        city={city}
                        saved={favorited.has(post.id)}
                        routeOpen={routeOpen === post.id}
                        likeBusy={likeBusy === post.id}
                        favoriteBusy={favoriteBusy === post.id}
                        onOpen={() => openPost(post)}
                        onLike={() => void toggleLike(post)}
                        onFavorite={() => void toggleFavorite(post)}
                        onShare={() => void sharePost(post)}
                        onAdopt={() => void adopt(post, parsed)}
                        onExport={(format) => exportPost(post, format)}
                        onTag={selectTag}
                        onToggleRoute={() => setRouteOpen((current) => current === post.id ? null : post.id)}
                      />
                    );
                  })}

                  {sparse && (
                    <section className="cj-invite" aria-labelledby="cj-invite-title">
                      <p className="cj-invite-eyebrow">AN OPEN PAGE / 待续</p>
                      <h3 id="cj-invite-title">这一季的路书还不多，正好由你补上这一页。</h3>
                      <p>写一份走过的手记，或者把满意的路线分享出来。第一份完整手记，会成为后来者的路标。</p>
                      <div className="cj-invite-actions">
                        <button type="button" className="cj-invite-primary" onClick={() => setComposerOpen(true)}><Feather aria-hidden="true" />写一份新手记</button>
                        <button type="button" className="cj-invite-secondary" onClick={() => { setSearchQuery(''); setActiveTag(''); setViewMode('discover'); }}>浏览全部路书<ArrowUpRight aria-hidden="true" /></button>
                      </div>
                    </section>
                  )}
                </>
              )}
            </section>

            <aside className="cj-aside" aria-label="个人旅人手札">
              <PassportCard
                user={currentUser}
                season={season}
                myCount={myPosts.length}
                savedCount={favorited.size}
                receivedCount={receivedCount}
                stamps={stamps}
                onWrite={() => setComposerOpen(true)}
                onSaved={() => setViewMode('saved')}
                onMine={() => setViewMode('mine')}
              />
              <TrustCard />
            </aside>
          </div>
        </div>
      </div>

      {activePost && (
        <RouteDrawer
          post={activePost}
          parsed={activeParsed}
          nodes={activeNodes}
          city={activeCity}
          comments={commentsMap[activePost.id] || []}
          saved={favorited.has(activePost.id)}
          likeBusy={likeBusy === activePost.id}
          favoriteBusy={favoriteBusy === activePost.id}
          commentText={commentText}
          replyingTo={replyingTo}
          onClose={closeDrawer}
          onLike={() => void toggleLike(activePost)}
          onFavorite={() => void toggleFavorite(activePost)}
          onShare={() => void sharePost(activePost)}
          onAdopt={() => void adopt(activePost, activeParsed)}
          onExport={(format) => exportPost(activePost, format)}
          onSendComment={() => void sendComment(activePost.id)}
          onCommentText={setCommentText}
          onReply={(comment) => { setReplyingTo(comment); setCommentText(`@${comment.author} `); }}
          onTag={(tag) => { selectTag(tag); }}
        />
      )}

      {composerOpen && (
        <ComposerModal
          title={title}
          city={city}
          content={content}
          tags={composerTags}
          publishing={publishing}
          onTitle={setTitle}
          onCity={setCity}
          onContent={setContent}
          onTags={setComposerTags}
          onClose={() => setComposerOpen(false)}
          onSubmit={() => void publish()}
        />
      )}

      {toast && (
        <div className="cj-toast" role="status">
          <Check aria-hidden="true" /><span>{toast}</span>
        </div>
      )}
    </main>
  );
}

/* ------------------------------------------------------------------ */
/* Browser helpers ----------------------------------------------------- */
/* ------------------------------------------------------------------ */

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
