'use client';

import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  Compass,
  Lock,
  RefreshCw,
  RotateCcw,
  Sparkles,
  ZoomIn,
  ZoomOut,
  Layers,
  Activity,
  CheckCircle2,
  X,
  Users,
  Sliders,
  Trash2,
  Undo2,
} from 'lucide-react';
import { apiJson } from '../lib/apiClient';

type TraitNodeKind = 'self' | 'dimension' | 'budget' | 'tag' | 'feedback';

type TraitNode = {
  id: string;
  label: string;
  kind: TraitNodeKind;
  weight: number;
  evidence_count: number;
  source: string;
  // 可选：后端若返回房间协同重合标记则优先使用
  shared_with_room?: boolean;
  room_overlap_count?: number;
};

type TraitEdge = {
  from: string;
  to: string;
  relation: string;
  weight: number;
  evidence_count: number;
  note?: string;
  source: string;
};

type TraitGraph = {
  graph_version?: string;
  owner_scope?: string;
  room_code?: string;
  room_member_count?: number;
  nodes?: TraitNode[];
  edges?: TraitEdge[];
  sample_count?: number;
  empty?: boolean;
  confidence?: string;
  disclosures?: string[];
};

export interface UserTraitGraphCardProps {
  className?: string;
  /** 可选传入当前房间号（如主界面的 SS38W3） */
  roomCode?: string;
  /** 节点权重微调回调 */
  onWeightChange?: (nodeId: string, newWeight: number) => void;
  /** 节点移出图谱回调 */
  onRemoveNode?: (nodeId: string) => void;
}

type ToneName = 'accent' | 'good' | 'risk' | 'warn' | 'ink';

const TONE: Record<
  ToneName,
  { fill: string; activeFill: string; stroke: string; text: string; badgeBg: string }
> = {
  accent: {
    fill: '#FFFFFF',
    activeFill: 'var(--pj-soft, #FFF7ED)',
    stroke: 'var(--pj-accent, #F97316)',
    text: 'var(--pj-accent-ink, #C2410C)',
    badgeBg: 'var(--pj-soft, #FFEDD5)',
  },
  good: {
    fill: '#FFFFFF',
    activeFill: '#ECFDF5',
    stroke: '#10B981',
    text: '#047857',
    badgeBg: '#D1FAE5',
  },
  risk: {
    fill: '#FFFFFF',
    activeFill: '#FFF1F2',
    stroke: '#F43F5E',
    text: '#BE123C',
    badgeBg: '#FFE4E6',
  },
  warn: {
    fill: '#FFFFFF',
    activeFill: '#FFFBEB',
    stroke: '#F59E0B',
    text: '#B45309',
    badgeBg: '#FEF3C7',
  },
  ink: {
    fill: '#FFFFFF',
    activeFill: '#F5F5F4',
    stroke: '#78716C',
    text: '#292524',
    badgeBg: '#E7E5E4',
  },
};

const RELATION_META: Record<string, { label: string; tone: ToneName }> = {
  composition_weight: { label: '兴趣配比', tone: 'accent' },
  budget_tendency: { label: '预算倾向', tone: 'warn' },
  likes: { label: '心仪偏好', tone: 'accent' },
  avoids: { label: '避雷标签', tone: 'risk' },
  cuisine: { label: '风味偏好', tone: 'warn' },
  trip_tag: { label: '行程印记', tone: 'accent' },
  feedback_up: { label: '正向反馈', tone: 'good' },
  feedback_down: { label: '负向反馈', tone: 'risk' },
};

const KIND_META: Record<TraitNodeKind, { label: string; filter: FilterKey }> = {
  self: { label: '核心画像', filter: 'all' },
  dimension: { label: '偏好维度', filter: 'dimension' },
  budget: { label: '预算锚点', filter: 'dimension' },
  tag: { label: '兴趣标签', filter: 'tag' },
  feedback: { label: '真实反馈', filter: 'feedback' },
};

const SOURCE_META: Record<string, string> = {
  user_preference: '偏好设置',
  feedback_log: '行程反馈记录',
  trip_plan: '历史生成方案',
  session: '实时协同会话',
};

const CONFIDENCE_META: Record<string, { label: string; color: string; dot: string }> = {
  high: {
    label: '画像成熟 · 样本充足',
    color: 'text-emerald-700 bg-emerald-50 border-emerald-200/80',
    dot: 'bg-emerald-500',
  },
  medium: {
    label: '持续演化 · 样本积累中',
    color: 'text-orange-700 bg-orange-50 border-orange-200/80',
    dot: 'bg-orange-500',
  },
  low: {
    label: '初识阶段 · 样本偏少',
    color: 'text-stone-600 bg-stone-100 border-stone-200/80',
    dot: 'bg-stone-400',
  },
};

type FilterKey = 'all' | 'dimension' | 'tag' | 'feedback';

// 星图画布几何常量
const CX = 400;
const CY = 270;
const SELF_R = 40;
const INNER_ORBIT_R = 142;
const OUTER_ORBIT_R = 248;
const MAX_VISIBLE_NODES = 14;

function shortLabel(label: string) {
  const runes = Array.from(label);
  return runes.length > 9 ? `${runes.slice(0, 8).join('')}…` : label;
}

function getBadgeText(node: TraitNode): string {
  if (node.kind === 'dimension' && node.weight > 0) {
    return `${Math.round(node.weight * 100)}%`;
  }
  if (node.weight > 0 && node.weight <= 1) {
    return `${Math.round(node.weight * 100)}%`;
  }
  if (node.evidence_count > 0) {
    return `${node.evidence_count}次`;
  }
  return '';
}

function chipWidth(label: string, badge: string) {
  let units = 0;
  Array.from(label).forEach((ch) => {
    units += /[\u3000-\u9fff\uff00-\uffef]/.test(ch) ? 1 : 0.58;
  });
  const badgeExtra = badge ? badge.length * 7 + 16 : 0;
  return Math.max(94, Math.min(214, Math.round(units * 13.5 + 34 + badgeExtra)));
}

function dominantEdges(edges: TraitEdge[]) {
  const byTarget = new Map<string, TraitEdge>();
  const rank = (edge: TraitEdge) => (edge.evidence_count || 0) * 10 + (edge.weight || 0);
  edges.forEach((edge) => {
    const prev = byTarget.get(edge.to);
    if (!prev || rank(edge) > rank(prev)) byTarget.set(edge.to, edge);
  });
  return byTarget;
}

function toneOf(node: TraitNode, edge?: TraitEdge): ToneName {
  const relation = edge?.relation || '';
  if (RELATION_META[relation]) return RELATION_META[relation].tone;
  if (node.kind === 'dimension') return 'accent';
  if (node.kind === 'budget') return 'warn';
  if (node.kind === 'feedback') return 'good';
  return 'ink';
}

/**
 * 判断节点是否为「房间多人共识偏好」：
 * 只认后端算出的 shared_with_room（同房间至少 2 位成员都有的节点）。
 * 没有该字段就按「不是共识」处理 —— 前端不做任何推导，
 * 否则会把个人权重当成同行共识，等于编造数据。
 */
function isRoomConsensusNode(node: TraitNode): boolean {
  return node.shared_with_room === true;
}

type Placed = {
  node: TraitNode;
  label: string;
  badge: string;
  x: number;
  y: number;
  w: number;
  h: number;
  angle: number;
  orbit: 'inner' | 'outer';
  tone: ToneName;
  edge?: TraitEdge;
  isShared: boolean;
};

type Layout = {
  items: Placed[];
  hidden: number;
  box: { x: number; y: number; w: number; h: number };
};

function buildOrbitLayout(nodes: TraitNode[], edgeMap: Map<string, TraitEdge>): Layout {
  const visible = nodes.slice(0, MAX_VISIBLE_NODES);

  const innerList: TraitNode[] = [];
  const outerList: TraitNode[] = [];

  visible.forEach((node) => {
    if (node.kind === 'dimension' || node.kind === 'budget') {
      innerList.push(node);
    } else {
      outerList.push(node);
    }
  });

  if (innerList.length === 0 && outerList.length > 4) {
    innerList.push(...outerList.splice(0, 4));
  } else if (innerList.length > 6) {
    outerList.unshift(...innerList.splice(6));
  }

  const placeRing = (
    ringNodes: TraitNode[],
    radiusX: number,
    radiusY: number,
    orbit: 'inner' | 'outer',
    angleOffset: number
  ): Placed[] => {
    const count = ringNodes.length;
    if (count === 0) return [];

    return ringNodes.map((node, i) => {
      const angle = angleOffset + (i * 2 * Math.PI) / count;
      const x = CX + Math.cos(angle) * radiusX;
      const y = CY + Math.sin(angle) * radiusY;
      const label = shortLabel(node.label);
      const badge = getBadgeText(node);
      const h = orbit === 'inner' ? 38 : 34;
      const edge = edgeMap.get(node.id);

      return {
        node,
        label,
        badge,
        x,
        y,
        w: chipWidth(label, badge),
        h,
        angle,
        orbit,
        tone: toneOf(node, edge),
        edge,
        isShared: isRoomConsensusNode(node),
      };
    });
  };

  const innerPlaced = placeRing(
    innerList,
    INNER_ORBIT_R * 1.15,
    INNER_ORBIT_R * 0.88,
    'inner',
    -Math.PI / 4
  );
  const outerPlaced = placeRing(
    outerList,
    OUTER_ORBIT_R * 1.22,
    OUTER_ORBIT_R * 0.86,
    'outer',
    -Math.PI / 6 + (innerList.length > 0 ? Math.PI / innerList.length : 0)
  );

  return {
    items: [...innerPlaced, ...outerPlaced],
    hidden: Math.max(0, nodes.length - visible.length),
    box: { x: 0, y: 0, w: 800, h: 540 },
  };
}

export default function UserTraitGraphCard({
  className = '',
  roomCode,
  onWeightChange,
  onRemoveNode,
}: UserTraitGraphCardProps) {
  const uid = useId().replace(/[:]/g, '');

  const [graph, setGraph] = useState<TraitGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [filter, setFilter] = useState<FilterKey>('all');
  const [selectedId, setSelectedId] = useState('');
  const [hoverId, setHoverId] = useState('');
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ id: number; x: number; y: number; ox: number; oy: number } | null>(null);
  const starCanvasRef = useRef<HTMLDivElement | null>(null);

  // 移动端星图保留完整信息密度，靠横向滑动查看；首次进入默认居中聚焦。
  useEffect(() => {
    const el = starCanvasRef.current;
    if (!el) return;
    const center = () => {
      if (window.matchMedia('(max-width: 767px)').matches && el.scrollWidth > el.clientWidth) {
        el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2;
      } else {
        el.scrollLeft = 0;
      }
    };
    const timer = window.setTimeout(center, 60);
    window.addEventListener('resize', center);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('resize', center);
    };
  }, [graph]);

  // 新增状态 1：多人协同房间交集对比模式
  const [roomCompareMode, setRoomCompareMode] = useState(false);

  // 新增状态 2：本地实时权重覆盖与节点剔除（支持一键撤销）
  const [weightOverrides, setWeightOverrides] = useState<Record<string, number>>({});
  const [removedIds, setRemovedIds] = useState<string[]>([]);
  const [lastRemovedNode, setLastRemovedNode] = useState<TraitNode | null>(null);
  // 权重/移出操作失败的可见提示：不再静默吞掉，否则用户以为已经保存。
  const [actionError, setActionError] = useState('');

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError('');
    // 带上房间号，后端才能算出真实的「房间共同交集」。
    const endpoint = roomCode
      ? `/api/v1/user/preference/graph?room_code=${encodeURIComponent(roomCode)}`
      : '/api/v1/user/preference/graph';
    apiJson<{ data?: TraitGraph } & TraitGraph>(endpoint)
      .then((response) => {
        if (!alive) return;
        setGraph(response?.data || response || null);
      })
      .catch((err) => {
        if (!alive) return;
        setGraph(null);
        setError(err instanceof Error ? err.message : '图谱暂时不可用');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [reloadKey, roomCode]);

  // 合并本地权重微调与过滤已剔除节点
  const nodes = useMemo(() => {
    const removedSet = new Set(removedIds);
    return (graph?.nodes || [])
      .filter((node) => node.kind !== 'self' && !removedSet.has(node.id))
      .map((node) =>
        weightOverrides[node.id] !== undefined
          ? { ...node, weight: weightOverrides[node.id] }
          : node
      )
      .sort(
        (a, b) =>
          (b.evidence_count || 0) - (a.evidence_count || 0) || (b.weight || 0) - (a.weight || 0)
      );
  }, [graph, weightOverrides, removedIds]);

  const edgeMap = useMemo(() => dominantEdges(graph?.edges || []), [graph]);

  const counts = useMemo(() => {
    const base = { dimension: 0, tag: 0, feedback: 0 };
    nodes.forEach((node) => {
      if (node.kind === 'dimension' || node.kind === 'budget') base.dimension += 1;
      else if (node.kind === 'tag') base.tag += 1;
      else if (node.kind === 'feedback') base.feedback += 1;
    });
    return base;
  }, [nodes]);

  const filtered = useMemo(() => {
    if (filter === 'all') return nodes;
    if (filter === 'dimension') {
      return nodes.filter((node) => node.kind === 'dimension' || node.kind === 'budget');
    }
    return nodes.filter((node) => node.kind === filter);
  }, [nodes, filter]);

  const layout = useMemo(() => buildOrbitLayout(filtered, edgeMap), [filtered, edgeMap]);
  const placed = layout.items;
  const box = layout.box;

  // 统计房间共识交集数据
  const sharedPlaced = useMemo(() => placed.filter((item) => item.isShared), [placed]);
  // 没有房间号、或房间里不足 2 位成员时，不存在"共同交集"，不展示对比入口。
  const activeRoomCode = (roomCode || graph?.room_code || '').toUpperCase();
  const roomSharedAvailable = Boolean(activeRoomCode) && (graph?.room_member_count || 0) >= 2;

  const samples = graph?.sample_count ?? 0;
  const confMeta =
    (graph?.confidence && CONFIDENCE_META[graph.confidence]) ||
    (samples > 0
      ? CONFIDENCE_META.low
      : { label: '尚无样本', color: 'text-stone-500 bg-stone-100', dot: 'bg-stone-400' });

  const isEmpty = Boolean(graph?.empty) || nodes.length === 0;
  const focusId = hoverId || selectedId;
  const active = placed.find((item) => item.node.id === focusId);
  const topRecords = nodes.filter((node) => node.evidence_count > 0).slice(0, 4);
  const dimensions = nodes.filter((node) => node.kind === 'dimension');

  useEffect(() => {
    if (filter !== 'all' && selectedId) {
      const stillVisible = filtered.some((node) => node.id === selectedId);
      if (!stillVisible) setSelectedId('');
    }
  }, [filter, filtered, selectedId]);

  useEffect(() => {
    if (!roomSharedAvailable) setRoomCompareMode(false);
  }, [roomSharedAvailable]);

  const selectNode = (id: string) => {
    setSelectedId((prev) => (prev === id ? '' : id));
  };

  // 处理节点权重微调
  const handleWeightSliderChange = (node: TraitNode, pctValue: number) => {
    const normalized = Math.round(pctValue) / 100;
    setWeightOverrides((prev) => ({ ...prev, [node.id]: normalized }));
    onWeightChange?.(node.id, normalized);

    // 同步后端持久化，失败时明确告知（否则刷新就还原，用户以为没生效）
    apiJson('/api/v1/user/preference/graph/node', {
      method: 'PATCH',
      body: JSON.stringify({ node_id: node.id, weight: normalized }),
    })
      .then(() => setActionError(''))
      .catch((err) => {
        setActionError(
          `「${node.label}」权重保存失败：${err instanceof Error ? err.message : '请稍后重试'}`
        );
      });
  };

  // 处理移出过时节点
  const handleRemoveActiveNode = (node: TraitNode) => {
    setRemovedIds((prev) => [...prev, node.id]);
    setLastRemovedNode(node);
    setSelectedId('');
    setHoverId('');
    onRemoveNode?.(node.id);

    apiJson(`/api/v1/user/preference/graph/node/${encodeURIComponent(node.id)}`, {
      method: 'DELETE',
    })
      .then(() => setActionError(''))
      .catch((err) => {
        // 后端没删成功就不要在前端假装删掉了，否则刷新后节点又回来。
        setRemovedIds((prev) => prev.filter((id) => id !== node.id));
        setLastRemovedNode((prev) => (prev?.id === node.id ? null : prev));
        setActionError(
          `「${node.label}」移出失败：${err instanceof Error ? err.message : '请稍后重试'}`
        );
      });
  };

  // 撤销移出：同时告诉后端恢复该节点，否则刷新后仍然被过滤。
  const handleUndoRemove = () => {
    if (!lastRemovedNode) return;
    const node = lastRemovedNode;
    setRemovedIds((prev) => prev.filter((id) => id !== node.id));
    setSelectedId(node.id);
    setLastRemovedNode(null);

    apiJson('/api/v1/user/preference/graph/node', {
      method: 'PATCH',
      body: JSON.stringify({ node_id: node.id, hidden: false }),
    })
      .then(() => setActionError(''))
      .catch((err) => {
        setActionError(
          `「${node.label}」恢复失败：${err instanceof Error ? err.message : '请稍后重试'}`
        );
      });
  };

  const zoomTo = (next: number) => {
    setView({ scale: Math.max(0.8, Math.min(1.8, Math.round(next * 100) / 100)), x: 0, y: 0 });
  };

  const handlePointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.pointerType !== 'mouse' || view.scale <= 1) return;
    dragRef.current = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      ox: view.x,
      oy: view.y,
    };
    setDragging(true);
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = box.w / (rect.width || box.w);
    const maxX = (box.w / 2) * (view.scale - 1);
    const maxY = (box.h / 2) * (view.scale - 1);
    setView((prev) => ({
      ...prev,
      x: Math.max(-maxX, Math.min(maxX, drag.ox + (event.clientX - drag.x) * ratio)),
      y: Math.max(-maxY, Math.min(maxY, drag.oy + (event.clientY - drag.y) * ratio)),
    }));
  };

  const handlePointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    dragRef.current = null;
    setDragging(false);
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  const transform = `translate(${view.x} ${view.y}) translate(${CX} ${CY}) scale(${view.scale}) translate(${-CX} ${-CY})`;

  return (
    <section
      className={`rounded-3xl border border-stone-200/80 bg-white p-6 sm:p-7 shadow-[0_8px_30px_rgba(28,25,23,0.04)] text-stone-800 ${className}`}
      aria-labelledby="trait-graph-heading"
      data-testid="user-trait-graph"
    >
      {/* 顶部标题区 + 房间协同交集对比开关 */}
      <div className="pj-star-head flex flex-wrap items-start justify-between gap-4 pb-5 border-b border-stone-100">
        <div className="min-w-0">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-orange-50 border border-orange-200/60 text-[11px] font-bold text-orange-600 tracking-wide">
            <Compass className="w-3.5 h-3.5" />
            <span>个人旅行基因图谱</span>
          </div>
          <h2
            id="trait-graph-heading"
            className="mt-2.5 text-xl sm:text-2xl font-extrabold tracking-tight text-stone-900"
          >
            你的旅行偏好星图
          </h2>
          <p className="mt-1 max-w-xl text-xs sm:text-sm text-stone-500 leading-relaxed">
            {roomSharedAvailable
              ? '支持实时微调节点引力权重，或开启「房间交集对比」一眼锁定同行好友的共同偏好。'
              : '支持实时微调节点引力权重、移出过时标签；调整后会保存到你的账号。'}
          </p>
        </div>

        {/* 右上角：协同交集模式切换器 + 隐私标识 */}
        <div className="flex flex-wrap items-center gap-2.5">
          {roomSharedAvailable && (
            <div className="inline-flex p-1 rounded-full bg-paper-hover border border-stone-200/70">
              <button
                type="button"
                onClick={() => setRoomCompareMode(false)}
                className={`px-3 py-1 rounded-full text-xs font-bold transition-all cursor-pointer ${
                  !roomCompareMode
                    ? 'bg-white text-stone-900 shadow-2xs'
                    : 'text-stone-500 hover:text-stone-800'
                }`}
              >
                个人星图
              </button>
              <button
                type="button"
                onClick={() => setRoomCompareMode(true)}
                className={`px-3 py-1 rounded-full text-xs font-bold flex items-center gap-1.5 transition-all cursor-pointer ${
                  roomCompareMode
                    ? 'bg-accent text-white shadow-xs shadow-orange-500/25'
                    : 'text-stone-600 hover:text-orange-600'
                }`}
              >
                <Users className="w-3.5 h-3.5" />
                <span>房间共同交集</span>
                <span
                  className={`text-[10px] px-1.5 py-0.2 rounded-full font-extrabold tabular-nums ${
                    roomCompareMode ? 'bg-white/20 text-white' : 'bg-orange-100 text-orange-600'
                  }`}
                >
                  {activeRoomCode}
                </span>
              </button>
            </div>
          )}

          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-stone-200/80 bg-paper-hover px-3 py-1.5 text-xs font-semibold text-stone-600">
            <Lock className="h-3.5 w-3.5 text-orange-500" />
            仅自己可见
          </span>
        </div>
      </div>

      {/* 加载态 */}
      {loading && (
        <div className="mt-6 rounded-2xl bg-paper-hover border border-stone-200/60 flex flex-col items-center justify-center gap-3 py-20">
          <div className="w-10 h-10 rounded-2xl bg-orange-500/10 flex items-center justify-center text-orange-500">
            <RefreshCw className="h-5 w-5 animate-spin" />
          </div>
          <p className="text-xs font-semibold text-stone-500">正在构建你的旅行偏好星图…</p>
        </div>
      )}

      {/* 错误态 */}
      {!loading && error && (
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-red-200/80 bg-red-50/60 px-5 py-4 text-xs font-semibold text-red-700">
          <span>图谱数据同步异常：{error}</span>
          <button
            type="button"
            onClick={() => setReloadKey((key) => key + 1)}
            className="rounded-xl border border-red-200 bg-white px-3.5 py-1.5 font-bold text-red-600 shadow-2xs transition hover:bg-red-50 cursor-pointer"
          >
            重新加载
          </button>
        </div>
      )}

      {/* 节点操作失败提示：权重/移出没写进账号时明确告知 */}
      {!loading && !error && actionError && (
        <div
          className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200/80 bg-amber-50/70 px-5 py-3 text-xs font-semibold text-amber-800"
          role="alert"
        >
          <span>{actionError}</span>
          <button
            type="button"
            onClick={() => setActionError('')}
            className="rounded-xl border border-amber-200 bg-white px-3 py-1.5 font-bold text-amber-700 shadow-2xs transition hover:bg-amber-100 cursor-pointer"
          >
            知道了
          </button>
        </div>
      )}

      {/* 空状态 */}
      {!loading && !error && isEmpty && (
        <div
          className="mt-6 rounded-2xl border border-dashed border-stone-200 bg-paper-hover px-6 py-14 text-center"
          data-testid="trait-graph-empty"
        >
          <div className="mx-auto w-12 h-12 rounded-2xl bg-orange-50 border border-orange-200/60 flex items-center justify-center text-orange-500">
            <Sparkles className="h-5 w-5" />
          </div>
          <p className="mt-3.5 text-base font-bold text-stone-800">你的旅行星图尚未点亮</p>
          <p className="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-stone-500">
            生成第一份协同行程、对推荐景点进行点赞或避雷，或在偏好设置中勾选心仪标签后，这里将自动生长出专属于你的兴趣引力图。
          </p>
          {lastRemovedNode && (
            <button
              type="button"
              onClick={handleUndoRemove}
              className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-orange-500 px-4 py-1.5 text-xs font-bold text-white shadow-xs hover:bg-orange-600 cursor-pointer"
            >
              <Undo2 className="w-3.5 h-3.5" />
              恢复刚移出的「{lastRemovedNode.label}」
            </button>
          )}
        </div>
      )}

      {/* 核心图谱展示区 */}
      {!loading && !error && !isEmpty && (
        <>
          {/* 顶部指标条与分段过滤器 */}
          <div className="pj-star-controls mt-5 flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-2.5">
              <div className="flex items-center gap-2 rounded-xl bg-paper-hover border border-stone-200/70 px-3.5 py-1.5">
                <Layers className="w-3.5 h-3.5 text-orange-500" />
                <span className="text-xs text-stone-500">偏好节点</span>
                <strong className="text-sm font-extrabold text-stone-900 tabular-nums">
                  {nodes.length}
                </strong>
              </div>

              <div className="flex items-center gap-2 rounded-xl bg-paper-hover border border-stone-200/70 px-3.5 py-1.5">
                <Activity className="w-3.5 h-3.5 text-orange-500" />
                <span className="text-xs text-stone-500">行为样本</span>
                <strong className="text-sm font-extrabold text-stone-900 tabular-nums">
                  {samples}
                </strong>
              </div>

              {roomCompareMode ? (
                <span className="inline-flex items-center gap-1.5 rounded-xl border border-amber-300/80 bg-amber-50 px-3 py-1.5 text-xs font-bold text-amber-800">
                  <span className="w-2 h-2 rounded-full bg-amber-500 animate-ping" />
                  房间 {activeRoomCode} 共识重合：{sharedPlaced.length} 项
                </span>
              ) : (
                <span
                  data-testid="trait-graph-confidence"
                  className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-bold ${confMeta.color}`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${confMeta.dot}`} />
                  {confMeta.label}
                </span>
              )}
            </div>

            {/* 分类筛选与画布缩放器 */}
            <div className="flex flex-wrap items-center gap-2">
              <div
                className="inline-flex p-1 rounded-xl bg-paper-hover border border-stone-200/60"
                role="group"
                aria-label="按节点类型筛选"
              >
                {(
                  [
                    ['all', '全部', nodes.length],
                    ['dimension', '核心维度', counts.dimension],
                    ['tag', '兴趣标签', counts.tag],
                    ['feedback', '真实反馈', counts.feedback],
                  ] as const
                ).map(([key, label, count]) => {
                  const activeTab = filter === key;
                  return (
                    <button
                      type="button"
                      key={key}
                      onClick={() => setFilter(key)}
                      disabled={count === 0}
                      aria-pressed={activeTab}
                      className={`px-3 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                        activeTab
                          ? 'bg-white text-stone-900 shadow-2xs'
                          : count === 0
                            ? 'text-stone-300 cursor-not-allowed'
                            : 'text-stone-600 hover:text-stone-900'
                      }`}
                    >
                      {label}
                      <span
                        className={`ml-1 text-[10px] tabular-nums ${
                          activeTab ? 'text-orange-600' : 'opacity-60'
                        }`}
                      >
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>

              <div className="inline-flex items-center rounded-xl border border-stone-200/80 bg-white px-1 py-0.5">
                <button
                  type="button"
                  onClick={() => zoomTo(view.scale - 0.15)}
                  disabled={view.scale <= 0.8}
                  aria-label="缩小图谱"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-stone-500 transition hover:bg-stone-100 hover:text-stone-900 disabled:opacity-35 cursor-pointer"
                >
                  <ZoomOut className="h-3.5 w-3.5" />
                </button>
                <span className="min-w-[42px] text-center text-[11px] font-bold tabular-nums text-stone-600">
                  {Math.round(view.scale * 100)}%
                </span>
                <button
                  type="button"
                  onClick={() => zoomTo(view.scale + 0.15)}
                  disabled={view.scale >= 1.8}
                  aria-label="放大图谱"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-stone-500 transition hover:bg-stone-100 hover:text-stone-900 disabled:opacity-35 cursor-pointer"
                >
                  <ZoomIn className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => zoomTo(1)}
                  disabled={view.scale === 1 && view.x === 0 && view.y === 0}
                  aria-label="重置视图"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-stone-500 transition hover:bg-stone-100 hover:text-stone-900 disabled:opacity-35 cursor-pointer"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </div>

          {/* 主体双栏：左侧双环星图画布 + 右侧 Bento 交互洞察面板
              高度必须固定：悬停节点会切换右侧内容，若高度随内容变化，画布就会被拉伸、
              节点随之位移，鼠标脱离节点后内容又缩回去，形成hover抖动/闪烁。 */}
          <div className="pj-star-layout mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_316px] lg:items-stretch lg:h-[480px]">
            {/* 左侧 SVG 星图容器 */}
            <div
              className="pj-star-canvas relative min-w-0 overflow-hidden rounded-2xl border border-stone-200/80 bg-paper-hover shadow-inner flex flex-col justify-between"
            >
              {/* 画布左上角动态图例说明 */}
              <div className="pointer-events-none absolute top-3.5 left-4 right-4 z-10 flex flex-wrap items-center justify-between gap-2 text-[11px] font-semibold">
                <div className="flex items-center gap-2.5">
                  <span className="inline-flex items-center gap-1.5 bg-white/85 backdrop-blur-xs px-2.5 py-1 rounded-full border border-stone-200/60 text-stone-500">
                    <span className="w-2 h-2 rounded-full border border-orange-400 bg-orange-100" />
                    内环：核心维度与预算
                  </span>
                  <span className="hidden sm:inline-flex items-center gap-1.5 bg-white/85 backdrop-blur-xs px-2.5 py-1 rounded-full border border-stone-200/60 text-stone-500">
                    <span className="w-2 h-2 rounded-full border border-stone-400 bg-stone-100" />
                    外环：标签与行为印记
                  </span>
                </div>

                <span className="pj-star-swipe-hint">左右滑动查看完整星图</span>

                {roomCompareMode && (
                  <span className="inline-flex items-center gap-1.5 bg-amber-50/95 backdrop-blur-xs px-3 py-1 rounded-full border border-amber-300/80 text-amber-800 font-bold shadow-2xs">
                    <span className="w-2 h-2 rounded-full bg-orange-500" />
                    金色双环 = 同行好友共同偏好
                  </span>
                )}
              </div>

              <div ref={starCanvasRef} className="pj-star-scroll relative min-h-0 flex-1">
                <svg
                  viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`}
                  className="w-full h-full min-h-[390px] select-none"
                  style={{
                    cursor: view.scale > 1 ? (dragging ? 'grabbing' : 'grab') : 'default',
                    touchAction: 'pan-x pan-y',
                  }}
                role="img"
                aria-label={`你的旅行特征图谱，共 ${nodes.length} 个节点，基于 ${samples} 条记录`}
                data-testid="trait-graph-svg"
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={handlePointerUp}
                onPointerCancel={handlePointerUp}
                onPointerLeave={() => setHoverId('')}
              >
                <defs>
                  <pattern
                    id={`${uid}-dots`}
                    width={22}
                    height={22}
                    patternUnits="userSpaceOnUse"
                  >
                    <circle cx={1.5} cy={1.5} r={1} fill="var(--pj-line-strong, #D6D3D1)" opacity={0.55} />
                  </pattern>

                  <radialGradient id={`${uid}-center-glow`} cx="50%" cy="50%" r="50%">
                    <stop offset="0%" stopColor="var(--pj-accent, #F97316)" stopOpacity="0.16" />
                    <stop offset="55%" stopColor="var(--pj-accent, #F97316)" stopOpacity="0.05" />
                    <stop offset="100%" stopColor="var(--pj-subtle, #FAF8F5)" stopOpacity="0" />
                  </radialGradient>

                  <linearGradient id={`${uid}-core-grad`} x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="var(--pj-accent, #FB923C)" />
                    <stop offset="100%" stopColor="var(--pj-accent-ink, #EA580C)" />
                  </linearGradient>
                </defs>

                <rect x={box.x} y={box.y} width={box.w} height={box.h} fill="var(--pj-subtle, #FAF8F5)" />
                <rect
                  x={box.x}
                  y={box.y}
                  width={box.w}
                  height={box.h}
                  fill={`url(#${uid}-dots)`}
                  onClick={() => setSelectedId('')}
                />

                <g transform={transform}>
                  <circle cx={CX} cy={CY} r={245} fill={`url(#${uid}-center-glow)`} />

                  {/* 内外环星轨 */}
                  <ellipse
                    cx={CX}
                    cy={CY}
                    rx={INNER_ORBIT_R * 1.15}
                    ry={INNER_ORBIT_R * 0.88}
                    fill="none"
                    stroke={roomCompareMode ? 'var(--pj-border, #FDBA74)' : 'var(--pj-line, #E7E5E4)'}
                    strokeWidth={1.2}
                    strokeDasharray="4 6"
                  />
                  <ellipse
                    cx={CX}
                    cy={CY}
                    rx={OUTER_ORBIT_R * 1.22}
                    ry={OUTER_ORBIT_R * 0.86}
                    fill="none"
                    stroke="var(--pj-line, #E7E5E4)"
                    strokeWidth={1}
                    strokeDasharray="2 8"
                    opacity={0.75}
                  />

                  {/* 能量连线层 */}
                  {placed.map((item) => {
                    const tone = TONE[item.tone];
                    const isFocus = focusId === item.node.id;
                    const highlightInRoom = roomCompareMode && item.isShared;
                    const dimmed =
                      (focusId !== '' && !isFocus) || (roomCompareMode && !item.isShared && !isFocus);

                    const dx = item.x - CX;
                    const dy = item.y - CY;
                    const dist = Math.hypot(dx, dy) || 1;
                    const startX = CX + (dx / dist) * (SELF_R + 4);
                    const startY = CY + (dy / dist) * (SELF_R + 4);

                    const midX = (startX + item.x) / 2 - dy * 0.12;
                    const midY = (startY + item.y) / 2 + dx * 0.12;
                    const d = `M ${startX} ${startY} Q ${midX} ${midY} ${item.x} ${item.y}`;

                    const weightVal = item.node.weight || item.edge?.weight || 0.6;
                    const strokeW = 1.4 + Math.min(2.6, Math.sqrt(Math.max(weightVal, 0.2)) * 1.3);

                    return (
                      <g key={`edge-${item.node.id}`}>
                        <path
                          d={d}
                          fill="none"
                          stroke={highlightInRoom ? 'var(--pj-accent, #F97316)' : tone.stroke}
                          strokeWidth={isFocus || highlightInRoom ? strokeW + 1 : strokeW}
                          strokeOpacity={
                            dimmed ? 0.08 : isFocus || highlightInRoom ? 0.82 : 0.28
                          }
                          strokeLinecap="round"
                        />
                        {(isFocus || highlightInRoom) && (
                          <path
                            d={d}
                            fill="none"
                            stroke={highlightInRoom ? 'var(--pj-accent-ink, #EA580C)' : tone.stroke}
                            strokeWidth={2.2}
                            strokeDasharray="5 6"
                            strokeLinecap="round"
                          >
                            <animate
                              attributeName="stroke-dashoffset"
                              from="22"
                              to="0"
                              dur={isFocus ? '0.7s' : '1.4s'}
                              repeatCount="indefinite"
                            />
                          </path>
                        )}
                      </g>
                    );
                  })}

                  {/* 中心节点：支持「单人你」与「房间多人共创」双态 */}
                  <g>
                    <circle
                      cx={CX}
                      cy={CY}
                      r={SELF_R + 12}
                      fill="none"
                      stroke="var(--pj-accent, #F97316)"
                      strokeWidth={1.3}
                      strokeDasharray={roomCompareMode ? '6 4' : '3 6'}
                      opacity={0.55}
                    />
                    <circle
                      cx={CX}
                      cy={CY}
                      r={SELF_R + 4}
                      fill="#FFFFFF"
                      stroke="var(--pj-border, #FED7AA)"
                      strokeWidth={1.5}
                    />
                    <circle cx={CX} cy={CY} r={SELF_R} fill={`url(#${uid}-core-grad)`} />
                    <text
                      x={CX}
                      y={CY + 1}
                      textAnchor="middle"
                      fontSize={roomCompareMode ? 13.5 : 16}
                      fontWeight={800}
                      fill="#FFFFFF"
                    >
                      {roomCompareMode ? '房间共识' : '你'}
                    </text>
                    <text
                      x={CX}
                      y={CY + 16}
                      textAnchor="middle"
                      fontSize={9}
                      fontWeight={700}
                      fill="var(--pj-soft, #FFEDD5)"
                      letterSpacing={0.6}
                    >
                      {roomCompareMode ? activeRoomCode : 'CORE'}
                    </text>
                  </g>

                  {/* 药丸胶囊节点层 */}
                  {placed.map((item) => {
                    const tone = TONE[item.tone];
                    const isFocus = focusId === item.node.id;
                    const isSelected = selectedId === item.node.id;
                    const highlightInRoom = roomCompareMode && item.isShared;
                    const dimmed =
                      (focusId !== '' && !isFocus) || (roomCompareMode && !item.isShared && !isFocus);
                    const rx = item.h / 2;

                    return (
                      <g
                        key={`node-${item.node.id}`}
                        style={{
                          opacity: dimmed ? 0.25 : 1,
                          transition: 'opacity 0.2s ease',
                          cursor: 'pointer',
                        }}
                        data-testid={`trait-node-${item.node.kind}`}
                        onMouseEnter={() => setHoverId(item.node.id)}
                        onMouseLeave={() => setHoverId('')}
                        onFocus={() => setHoverId(item.node.id)}
                        onBlur={() => setHoverId('')}
                        onClick={(event) => {
                          event.stopPropagation();
                          selectNode(item.node.id);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            selectNode(item.node.id);
                          }
                        }}
                        role="button"
                        tabIndex={0}
                        aria-label={`${KIND_META[item.node.kind].label}：${item.node.label}${
                          item.edge ? `，${RELATION_META[item.edge.relation]?.label || '关联'}` : ''
                        }`}
                        aria-pressed={isSelected}
                      >
                        <title>{`${item.node.label}${
                          item.node.evidence_count ? `（${item.node.evidence_count} 次记录）` : ''
                        }`}</title>

                        <g
                          style={{
                            transform: isFocus ? 'scale(1.06)' : 'scale(1)',
                            transformBox: 'fill-box',
                            transformOrigin: 'center',
                            transition: 'transform 0.18s cubic-bezier(0.22, 1, 0.36, 1)',
                            filter:
                              isFocus || highlightInRoom
                                ? 'drop-shadow(0 8px 18px rgba(249, 115, 22, 0.18))'
                                : 'drop-shadow(0 2px 6px rgba(28, 25, 23, 0.06))',
                          }}
                        >
                          {/* 房间交集模式下的琥珀金外发光双环 */}
                          {highlightInRoom && (
                            <rect
                              x={item.x - item.w / 2 - 4}
                              y={item.y - item.h / 2 - 4}
                              width={item.w + 8}
                              height={item.h + 8}
                              rx={rx + 4}
                              fill="none"
                              stroke="#F59E0B"
                              strokeWidth={1.6}
                              strokeDasharray="4 3"
                            />
                          )}

                          {/* 胶囊主体 */}
                          <rect
                            x={item.x - item.w / 2}
                            y={item.y - item.h / 2}
                            width={item.w}
                            height={item.h}
                            rx={rx}
                            fill={
                              isFocus || isSelected || highlightInRoom ? tone.activeFill : tone.fill
                            }
                            stroke={
                              isFocus || isSelected
                                ? tone.stroke
                                : highlightInRoom
                                  ? 'var(--pj-accent, #F97316)'
                                  : 'var(--pj-line, #E7E5E4)'
                            }
                            strokeWidth={isFocus || isSelected || highlightInRoom ? 2 : 1.2}
                          />

                          {/* 左侧语义色指示圆点 */}
                          <circle
                            cx={item.x - item.w / 2 + 14}
                            cy={item.y}
                            r={4}
                            fill={tone.stroke}
                          />

                          {/* 节点文案 */}
                          <text
                            x={item.badge ? item.x - 10 : item.x + 5}
                            y={item.y + 4.5}
                            textAnchor="middle"
                            fontSize={item.orbit === 'inner' ? 13 : 12.5}
                            fontWeight={700}
                            fill={isFocus || isSelected ? tone.text : 'var(--pj-ink, #292524)'}
                          >
                            {item.label}
                          </text>

                          {/* 右侧微徽章（展示百分比或频次） */}
                          {item.badge && (
                            <g>
                              <rect
                                x={item.x + item.w / 2 - 36}
                                y={item.y - 9}
                                width={28}
                                height={18}
                                rx={9}
                                fill={tone.badgeBg}
                              />
                              <text
                                x={item.x + item.w / 2 - 22}
                                y={item.y + 3.5}
                                textAnchor="middle"
                                fontSize={10}
                                fontWeight={800}
                                fill={tone.text}
                              >
                                {item.badge}
                              </text>
                            </g>
                          )}

                          {/* 房间交集角标 */}
                          {highlightInRoom && (
                            <g>
                              <rect
                                x={item.x + item.w / 2 - 26}
                                y={item.y - item.h / 2 - 9}
                                width={34}
                                height={15}
                                rx={7.5}
                                fill="var(--pj-accent, #F97316)"
                              />
                              <text
                                x={item.x + item.w / 2 - 9}
                                y={item.y - item.h / 2 + 1.5}
                                textAnchor="middle"
                                fontSize={8.5}
                                fontWeight={800}
                                fill="#FFFFFF"
                              >
                                ★共识
                              </text>
                            </g>
                          )}
                        </g>
                      </g>
                    );
                  })}
                </g>
              </svg>
              </div>

              {/* 分类为空浮层 */}
              {filtered.length === 0 && (
                <div className="absolute inset-0 flex items-center justify-center bg-paper-hover/60 backdrop-blur-[2px]">
                  <p className="rounded-xl bg-white border border-stone-200/80 shadow-xs px-4 py-2 text-xs font-bold text-stone-500">
                    该分类下暂无关联记录
                  </p>
                </div>
              )}

              {/* 底部画布图例栏 */}
              <div className="pj-star-legend px-4 py-2.5 bg-white/90 backdrop-blur-xs border-t border-stone-200/60 flex flex-wrap items-center justify-between gap-2 text-[11px] font-semibold text-stone-500">
                <div className="flex flex-wrap items-center gap-3">
                  {Object.entries(RELATION_META).map(([relation, item]) => (
                    <span key={relation} className="inline-flex items-center gap-1.5">
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{ backgroundColor: TONE[item.tone].stroke }}
                      />
                      {item.label}
                    </span>
                  ))}
                </div>
                {layout.hidden > 0 && (
                  <span className="text-stone-400">
                    已择优展示前 {MAX_VISIBLE_NODES} 项（其余 {layout.hidden} 项已折叠）
                  </span>
                )}
              </div>
            </div>

            {/* 右侧：Bento 风格洞察、权重微调与节点剔除面板 */}
            <aside
              className="min-w-0 min-h-0 rounded-2xl border border-stone-200/80 bg-paper-hover p-5 flex flex-col justify-between lg:overflow-y-auto lg:overscroll-contain"
              aria-live="polite"
              data-testid="trait-graph-inspector"
            >
              {active ? (
                /* 节点详情溯源 + 权重微调 + 剔除态 */
                <div className="space-y-4">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      <span
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold"
                        style={{
                          backgroundColor: TONE[active.tone].badgeBg,
                          color: TONE[active.tone].text,
                        }}
                      >
                        <span
                          className="w-1.5 h-1.5 rounded-full"
                          style={{ backgroundColor: TONE[active.tone].stroke }}
                        />
                        {KIND_META[active.node.kind].label}
                      </span>
                      {active.isShared && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 text-[11px] font-bold">
                          ★ 房间共识
                        </span>
                      )}
                    </div>

                    <button
                      type="button"
                      onClick={() => setSelectedId('')}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-stone-400 hover:text-stone-700 transition cursor-pointer"
                    >
                      <X className="w-3.5 h-3.5" />
                      关闭
                    </button>
                  </div>

                  <div className="bg-white rounded-2xl p-4 border border-stone-200/70 shadow-2xs space-y-3.5">
                    <div>
                      <h3 className="break-words text-lg font-extrabold text-stone-900">
                        {active.node.label}
                      </h3>
                      <p className="mt-0.5 text-xs font-semibold text-orange-600">
                        {active.edge
                          ? RELATION_META[active.edge.relation]?.label || '关联特征'
                          : '核心画像维度'}
                      </p>
                    </div>

                    <dl className="space-y-2 border-t border-stone-100 pt-3 text-xs">
                      <div className="flex items-center justify-between gap-3">
                        <dt className="text-stone-400 font-medium">数据来源</dt>
                        <dd className="font-bold text-stone-700">
                          {SOURCE_META[active.node.source] || active.node.source}
                        </dd>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <dt className="text-stone-400 font-medium">累计印记</dt>
                        <dd className="font-bold text-stone-700">
                          {active.node.evidence_count
                            ? `${active.node.evidence_count} 次行程/反馈触发`
                            : '偏好设置主动勾选'}
                        </dd>
                      </div>
                    </dl>

                    {/* 新增交互 1：实时引力权重滑块 */}
                    <div className="border-t border-stone-100 pt-3">
                      <div className="flex items-center justify-between text-xs mb-1.5">
                        <span className="font-bold text-stone-700 flex items-center gap-1.5">
                          <Sliders className="w-3.5 h-3.5 text-orange-500" />
                          引力权重微调
                        </span>
                        <span className="font-extrabold text-orange-600 tabular-nums">
                          {Math.round(
                            ((active.node.weight > 0 && active.node.weight <= 1
                              ? active.node.weight
                              : 0.65) || 0.65) * 100
                          )}
                          %
                        </span>
                      </div>
                      <input
                        type="range"
                        min={10}
                        max={100}
                        step={5}
                        value={Math.round(
                          ((active.node.weight > 0 && active.node.weight <= 1
                            ? active.node.weight
                            : 0.65) || 0.65) * 100
                        )}
                        onChange={(e) =>
                          handleWeightSliderChange(active.node, Number(e.target.value))
                        }
                        className="w-full h-1.5 bg-stone-200 rounded-lg appearance-none cursor-pointer accent-orange-500"
                      />
                      <div className="flex justify-between text-[10px] text-stone-400 mt-1">
                        <span>偶发参考</span>
                        <span>核心必选</span>
                      </div>
                    </div>

                    {active.edge?.note && (
                      <div className="rounded-xl bg-paper-hover border border-stone-200/60 px-3 py-2 text-xs leading-relaxed text-stone-600">
                        “{active.edge.note}”
                      </div>
                    )}

                    {/* 新增交互 2：移出过时节点按钮 */}
                    <div className="border-t border-stone-100 pt-3 flex items-center justify-between gap-2">
                      <span className="text-[11px] text-stone-400">不再符合当前口味？</span>
                      <button
                        type="button"
                        onClick={() => handleRemoveActiveNode(active.node)}
                        className="inline-flex items-center gap-1 rounded-xl border border-rose-200 bg-rose-50/70 hover:bg-rose-100 px-3 py-1.5 text-xs font-bold text-rose-600 transition cursor-pointer"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        移出图谱
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                /* 默认全局概览态（含房间交集摘要卡） */
                <div className="space-y-4">
                  <div>
                    <h3 className="text-sm font-extrabold text-stone-900">
                      {roomCompareMode ? `房间 ${activeRoomCode} 协同画像` : '画像构成概览'}
                    </h3>
                    <p className="mt-0.5 text-xs text-stone-500">
                      {roomCompareMode
                        ? '已高亮你与同行伙伴重合的兴趣锚点，生成方案时将优先满足。'
                        : '点击星图中的任意胶囊，可直接拖动滑块微调权重或剔除过时标签。'}
                    </p>
                  </div>

                  {/* 房间交集模式下置顶显示「共识重合卡」 */}
                  {roomCompareMode && (
                    <div className="bg-amber-50/80 rounded-2xl p-3.5 border border-amber-200/80 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-extrabold text-amber-900 flex items-center gap-1.5">
                          <Users className="w-3.5 h-3.5 text-orange-500" />
                          多人共识匹配度
                        </span>
                        <span className="text-sm font-black text-orange-600 tabular-nums">
                          {placed.length > 0
                            ? `${Math.round((sharedPlaced.length / placed.length) * 100)}%`
                            : '0%'}
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {sharedPlaced.slice(0, 6).map((item) => (
                          <button
                            key={item.node.id}
                            type="button"
                            onClick={() => selectNode(item.node.id)}
                            className="px-2.5 py-0.5 rounded-full bg-white border border-amber-300 text-[11px] font-bold text-amber-900 hover:border-orange-500 transition cursor-pointer"
                          >
                            ★ {item.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 核心维度配比条 */}
                  {dimensions.length > 0 && (
                    <div className="bg-white rounded-2xl p-4 border border-stone-200/70 space-y-2.5">
                      <p className="text-[11px] font-bold uppercase tracking-wider text-stone-400">
                        核心维度配比（点击可微调）
                      </p>
                      <div className="space-y-2.5">
                        {dimensions.map((node) => {
                          const pct = Math.round((node.weight || 0) * 100);
                          return (
                            <button
                              type="button"
                              key={node.id}
                              onClick={() => selectNode(node.id)}
                              className="group block w-full text-left cursor-pointer"
                            >
                              <div className="flex items-center justify-between text-xs font-bold text-stone-700 group-hover:text-orange-600 transition-colors">
                                <span>{node.label}</span>
                                <span className="tabular-nums text-stone-500 group-hover:text-orange-600">
                                  {pct}%
                                </span>
                              </div>
                              <div className="mt-1.5 h-2 rounded-full bg-stone-100 overflow-hidden">
                                <div
                                  className="h-full rounded-full bg-gradient-to-r from-orange-400 to-orange-500 transition-all duration-300"
                                  style={{ width: `${Math.max(6, Math.min(100, pct))}%` }}
                                />
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* 高频印记榜 */}
                  {topRecords.length > 0 && (
                    <div className="bg-white rounded-2xl p-4 border border-stone-200/70">
                      <p className="text-[11px] font-bold uppercase tracking-wider text-stone-400 mb-2">
                        高频旅行印记
                      </p>
                      <ul className="space-y-1">
                        {topRecords.map((node) => (
                          <li key={node.id}>
                            <button
                              type="button"
                              onClick={() => selectNode(node.id)}
                              className="flex w-full items-center justify-between rounded-xl px-2.5 py-2 text-left text-xs font-bold text-stone-700 transition hover:bg-orange-50/70 hover:text-orange-600 cursor-pointer"
                            >
                              <span className="truncate flex items-center gap-1.5">
                                <CheckCircle2 className="w-3.5 h-3.5 text-orange-500 shrink-0" />
                                {shortLabel(node.label)}
                              </span>
                              <span className="shrink-0 text-[11px] font-semibold text-stone-400 tabular-nums">
                                {node.evidence_count} 次记录
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* 底部：节点移出撤销提示条 + 披露说明 */}
              <div className="mt-4 pt-3 border-t border-stone-200/70 space-y-2">
                {lastRemovedNode && (
                  <div className="flex items-center justify-between gap-2 rounded-xl bg-stone-900 text-white px-3 py-2 text-xs">
                    <span className="truncate">已移出「{lastRemovedNode.label}」</span>
                    <button
                      type="button"
                      onClick={handleUndoRemove}
                      className="shrink-0 inline-flex items-center gap-1 font-bold text-orange-400 hover:text-orange-300 cursor-pointer"
                    >
                      <Undo2 className="w-3.5 h-3.5" />
                      撤销
                    </button>
                  </div>
                )}

                {(graph?.disclosures || []).length > 0 && (
                  <div className="space-y-1">
                    {(graph?.disclosures || []).map((line) => (
                      <p key={line} className="text-[11px] leading-relaxed text-stone-400">
                        {line}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            </aside>
          </div>
        </>
      )}
    </section>
  );
}