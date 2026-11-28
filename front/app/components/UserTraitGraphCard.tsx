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
  X,
  Users,
  Sliders,
  Trash2,
  Undo2,
  Star,
  Pause,
  Play,
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
    label: '样本积累中',
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

const DEFAULT_VIEW = { scale: 1, x: 0, y: 0 };
const FILTERS: Array<[Exclude<FilterKey, 'all'>, string]> = [
  ['dimension', '核心维度'],
  ['tag', '兴趣标签'],
  ['feedback', '真实反馈'],
];

// 星图画布几何常量
const CX = 400;
const CY = 270;
const SELF_R = 30;
const INNER_ORBIT_R = 108;
const OUTER_ORBIT_R = 172;
const ASTRO_R = 212;
const MAX_VISIBLE_NODES = 14;

const displayedWeight = (weight: number) => Number.isFinite(weight) ? Math.max(0, Math.min(1, weight)) : 0.65;

function OrbitComet({ radius, inner }: { radius: number; inner?: boolean }) {
  const angle = Math.PI / 4;
  const startX = CX - Math.sin(angle) * radius;
  const startY = CY - Math.cos(angle) * radius;
  const arc = `M ${startX} ${startY} A ${radius} ${radius} 0 0 1 ${CX} ${CY - radius}`;
  return <g className={`pj-star-comet${inner ? ' is-inner' : ''}`} aria-hidden="true" pointerEvents="none">
    <path d={arc} className="pj-star-comet-wake" pathLength="100" />
    <path d={arc} className="pj-star-comet-tail" pathLength="100" />
    <circle cx={CX} cy={CY - radius} r={inner ? 3 : 4} className="pj-star-comet-head" />
  </g>;
}

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
  const innerList: TraitNode[] = [];
  const outerList: TraitNode[] = [];

  const visible = nodes.slice(0, MAX_VISIBLE_NODES);
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
    INNER_ORBIT_R,
    INNER_ORBIT_R,
    'inner',
    -Math.PI / 2
  );
  const outerPlaced = placeRing(
    outerList,
    OUTER_ORBIT_R,
    OUTER_ORBIT_R,
    'outer',
    -Math.PI / 2 + Math.PI / Math.max(outerList.length, 3)
  );

  return {
    items: [...innerPlaced, ...outerPlaced],
    hidden: nodes.length - visible.length,
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
  const [animationPaused, setAnimationPaused] = useState(false);
  const [animationVisible, setAnimationVisible] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [selectedId, setSelectedId] = useState('');
  const [hoverId, setHoverId] = useState('');
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ id: number; x: number; y: number; ox: number; oy: number } | null>(null);
  const starCanvasRef = useRef<HTMLDivElement | null>(null);
  const inspectorRef = useRef<HTMLElement>(null);

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

  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const syncPreference = () => setReducedMotion(preference.matches);
    syncPreference();
    preference.addEventListener('change', syncPreference);
    return () => preference.removeEventListener('change', syncPreference);
  }, []);

  useEffect(() => {
    const canvas = starCanvasRef.current;
    if (!canvas || loading) return;
    let onScreen = false;
    const syncVisibility = () => setAnimationVisible(onScreen && !document.hidden);
    const observer = new IntersectionObserver(([entry]) => {
      onScreen = entry.isIntersecting;
      syncVisibility();
    });
    observer.observe(canvas);
    document.addEventListener('visibilitychange', syncVisibility);
    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', syncVisibility);
    };
  }, [graph, loading]);

  // 新增状态 1：多人协同房间交集对比模式
  const [roomCompareMode, setRoomCompareMode] = useState(false);

  // 新增状态 2：本地实时权重覆盖与节点剔除（支持一键撤销）
  const [weightOverrides, setWeightOverrides] = useState<Record<string, number>>({});
  const [weightDraft, setWeightDraft] = useState<{ id: string; value: number } | null>(null);
  const [savingWeight, setSavingWeight] = useState(false);
  const [saveNote, setSaveNote] = useState<{ id: string; text: string } | null>(null);
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
    const loadGraph = async () => {
      const response = await apiJson<{ data?: TraitGraph } & TraitGraph>(endpoint);
      let payload = response?.data || response || null;
      // 房间交集还没有样本时，退回个人图谱，避免整张星图空白。
      if (roomCode && (!payload?.nodes || payload.nodes.length === 0)) {
        const fallback = await apiJson<{ data?: TraitGraph } & TraitGraph>('/api/v1/user/preference/graph');
        payload = fallback?.data || fallback || payload;
      }
      return payload;
    };
    loadGraph()
      .then((payload) => {
        if (!alive) return;
        setGraph(payload);
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
      .sort(
        (a, b) =>
          (b.evidence_count || 0) - (a.evidence_count || 0) || (b.weight || 0) - (a.weight || 0)
      )
      .map(node => weightOverrides[node.id] === undefined ? node : { ...node, weight: weightOverrides[node.id] });
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

  useEffect(() => {
    if (filter !== 'all' && counts[filter] === 0) setFilter('all');
  }, [counts, filter]);

  const filtered = useMemo(() => {
    if (filter === 'all') return nodes;
    return nodes.filter(node => KIND_META[node.kind].filter === filter);
  }, [nodes, filter]);

  const layout = useMemo(() => buildOrbitLayout(filtered, edgeMap), [filtered, edgeMap]);
  const placed = layout.items;
  const box = layout.box;

  // 统计房间共识交集数据
  const sharedPlaced = useMemo(() => placed.filter((item) => item.isShared), [placed]);
  const sharedCount = nodes.filter(isRoomConsensusNode).length;
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
  const selected = placed.find((item) => item.node.id === selectedId);
  const active = selected ?? placed.find((item) => item.node.id === hoverId);
  const focusId = active?.node.id ?? '';
  const activeWeightPct = active ? Math.round(weightFor(active.node) * 100) : 0;

  function weightFor(node: TraitNode) {
    return weightDraft?.id === node.id ? weightDraft.value : displayedWeight(node.weight);
  }

  function nodeOffset(item: Placed) {
    // Keep each kind on its own ring and make weight changes visible without moving neighboring stars.
    const distance = (0.5 - weightFor(item.node)) * 38;
    return { x: Math.cos(item.angle) * distance, y: Math.sin(item.angle) * distance };
  }

  useEffect(() => {
    if (!roomSharedAvailable) setRoomCompareMode(false);
  }, [roomSharedAvailable]);

  const selectNode = (id: string, revealOnSmallScreen = false) => {
    const next = selectedId === id ? '' : id;
    setSelectedId(next);
    setHoverId('');
    setWeightDraft(null);
    setSaveNote(null);
    if (next && revealOnSmallScreen && window.matchMedia('(max-width: 1023px)').matches) {
      window.requestAnimationFrame(() => inspectorRef.current?.scrollIntoView({ block: 'nearest', behavior: reducedMotion ? 'instant' : 'smooth' }));
    }
  };

  function resetSelection() {
    setSelectedId('');
    setHoverId('');
    setWeightDraft(null);
    setSaveNote(null);
  }

  const saveWeight = async () => {
    if (!selected || !weightDraft || weightDraft.id !== selected.node.id || savingWeight) return;
    const { id, value } = weightDraft;
    setSavingWeight(true);
    setSaveNote(null);
    try {
      await apiJson('/api/v1/user/preference/graph/node', {
        method: 'PATCH', body: JSON.stringify({ node_id: id, weight: value }),
      });
      setWeightOverrides(prev => ({ ...prev, [id]: value }));
      setWeightDraft(current => current?.id === id && current.value === value ? null : current);
      onWeightChange?.(id, value);
      setActionError('');
      setSaveNote({ id, text: '偏好已保存' });
    } catch {
      setSaveNote({ id, text: '暂时无法保存，可以再试一次。' });
    } finally {
      setSavingWeight(false);
    }
  };

  // 处理移出过时节点
  const handleRemoveActiveNode = (node: TraitNode) => {
    setRemovedIds((prev) => [...prev, node.id]);
    setLastRemovedNode(node);
    resetSelection();
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
  const zoomIn = () => zoomTo(view.scale + 0.1);
  const zoomOut = () => zoomTo(view.scale - 0.1);

  const handlePointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.pointerType !== 'mouse' || event.button !== 0 || view.scale <= 1 || (event.target as Element).closest('[role="button"]')) return;
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
    if (dragRef.current?.id !== event.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const transform = `translate(${view.x} ${view.y}) translate(${CX} ${CY}) scale(${view.scale}) translate(${-CX} ${-CY})`;
  const motionState = reducedMotion ? 'reduced' : animationPaused || !animationVisible ? 'paused' : 'playing';
  const activeOffset = active && nodeOffset(active);

  function switchFilter(next: FilterKey) {
    setFilter(next);
    setView(DEFAULT_VIEW);
    resetSelection();
  }

  return (
    <section
      className={`rounded-3xl border border-stone-200/80 bg-white p-6 sm:p-7 shadow-[0_8px_30px_rgba(28,25,23,0.04)] text-stone-800 ${className}`}
      aria-labelledby="trait-graph-heading"
      data-testid="user-trait-graph"
    >
      {/* 顶部标题区 + 房间协同交集对比开关 */}
      <div className="pj-star-head flex flex-wrap items-start justify-between gap-4">
        <div className="pj-star-head-copy min-w-0">
          <div className="pj-star-eyebrow-row">
            <span className="pj-star-gene-badge">
              <Compass className="w-3.5 h-3.5" />
              <span>个人旅行基因图谱</span>
            </span>
            <span className="pj-star-fact">
              <Layers className="w-3.5 h-3.5" />
              偏好节点 <strong>{nodes.length}</strong>
            </span>
            <span className="pj-star-fact">
              <Activity className="w-3.5 h-3.5" />
              行为样本 <strong>{samples}</strong>
            </span>
            <span
              data-testid="trait-graph-confidence"
              className={`pj-star-confidence ${confMeta.color}`}
            >
              <span className={`pj-star-confidence-dot ${confMeta.dot}`} />
              {confMeta.label}
            </span>
          </div>
          <h2
            id="trait-graph-heading"
            className="pj-star-title"
          >
            你的旅行偏好星图
          </h2>
          <p className="pj-star-subtitle">
            {roomSharedAvailable
              ? '点亮一颗星查看它的来历，或切换到房间视图，找到你和同行人的共同偏好。'
              : '点亮一颗星，看看是什么塑造了你的旅行偏好。越靠近圆心，代表你越看重它。'}
          </p>
          {graph?.disclosures?.[0] && (
            <p className="pj-star-disclosure">{graph.disclosures[0]}</p>
          )}
        </div>

        {/* 右上角：协同交集模式切换器 + 隐私标识 */}
        <div className="pj-star-head-aside">
          {!loading && !error && !isEmpty && (
            <button type="button" onClick={() => setAnimationPaused(paused => !paused)} disabled={reducedMotion} className="pj-star-motion-toggle">
              {animationPaused || reducedMotion ? <Play size={15} aria-hidden="true" /> : <Pause size={15} aria-hidden="true" />}
              {reducedMotion ? '动效已减少' : animationPaused ? '播放动效' : '暂停动效'}
            </button>
          )}
          {roomSharedAvailable && (
            <div className="pj-star-room-switch">
              <button
                type="button"
                onClick={() => setRoomCompareMode(false)}
                className={!roomCompareMode ? 'is-active' : ''}
              >
                个人星图
              </button>
              <button
                type="button"
                onClick={() => setRoomCompareMode(true)}
                className={roomCompareMode ? 'is-active' : ''}
              >
                <Users className="w-3.5 h-3.5" />
                房间共同交集
                <span>{activeRoomCode}</span>
              </button>
            </div>
          )}

          <span className="pj-star-privacy">
            <Lock className="h-3.5 w-3.5" />
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
            选几个喜欢的旅行标签，或给行程里的地点留下反馈，你的偏好就会慢慢出现在这里。
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
          {/* 主体双栏：左侧双环星图画布 + 右侧 Bento 交互洞察面板
              高度必须固定：悬停节点会切换右侧内容，若高度随内容变化，画布就会被拉伸、
              节点随之位移，鼠标脱离节点后内容又缩回去，形成hover抖动/闪烁。 */}
          <div className="pj-star-layout">
            {/* 左侧 SVG 星图容器 */}
            <div
              className="pj-star-canvas relative min-w-0 overflow-hidden rounded-2xl border border-stone-200/80 bg-paper-hover shadow-inner flex flex-col justify-between"
              data-star-motion={motionState}
            >
              <div ref={starCanvasRef} className="pj-star-scroll relative min-h-0 flex-1">
                <svg
                  viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`}
                  className="w-full h-full min-h-[390px] select-none"
                  style={{
                    cursor: view.scale > 1 ? (dragging ? 'grabbing' : 'grab') : 'default',
                    touchAction: 'pan-x pan-y',
                  }}
                role="group"
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
                  onClick={resetSelection}
                />

                <g transform={transform}>
                  {/* 天体星盘：外圈 360° 刻度尺 */}
                  <circle cx={CX} cy={CY} r={ASTRO_R} fill="none" stroke="var(--pj-line-strong, #D6D3D1)" strokeWidth={1} />
                  <circle cx={CX} cy={CY} r={ASTRO_R - 8} fill="none" stroke="var(--pj-line, #E7E5E4)" strokeWidth={0.8} />
                  <g className="pj-star-tick-ring" pointerEvents="none">
                    {Array.from({ length: 72 }, (_, index) => {
                      const angle = ((index * 5) - 90) * Math.PI / 180;
                      const major = index % 6 === 0;
                      const innerR = ASTRO_R - (major ? 7 : 3);
                      return (
                        <line
                          key={`tick-${index}`}
                          x1={CX + Math.cos(angle) * ASTRO_R}
                          y1={CY + Math.sin(angle) * ASTRO_R}
                          x2={CX + Math.cos(angle) * innerR}
                          y2={CY + Math.sin(angle) * innerR}
                          stroke="var(--pj-ink-3, #A8A29E)"
                          strokeWidth={major ? 1 : 0.6}
                          opacity={major ? 0.55 : 0.34}
                        />
                      );
                    })}
                  </g>
                  {Array.from({ length: 12 }, (_, index) => {
                    const angle = ((index * 30) - 90) * Math.PI / 180;
                    return (
                      <text
                        key={`degree-${index}`}
                        x={CX + Math.cos(angle) * (ASTRO_R + 14)}
                        y={CY + Math.sin(angle) * (ASTRO_R + 14) + 3}
                        textAnchor="middle"
                        fontSize={12}
                        fontWeight={500}
                        fill="var(--pj-ink-3, #A8A29E)"
                        fontFamily="var(--pj-mono, ui-monospace, monospace)"                      >
                        {index * 30}°
                      </text>
                    );
                  })}

                  {/* 缓慢呼吸的中心柔光与星尘 */}
                  <circle className="pj-star-core-glow" cx={CX} cy={CY} r={ASTRO_R - 20} fill={`url(#${uid}-center-glow)`} pointerEvents="none" />
                  <g className="pj-star-dust" pointerEvents="none">
                    {Array.from({ length: 16 }, (_, index) => {
                      const angle = ((index * 137.5) - 90) * Math.PI / 180;
                      const radius = INNER_ORBIT_R + ((index * 31) % Math.max(1, OUTER_ORBIT_R - INNER_ORBIT_R));
                      return (
                        <circle
                          key={`dust-${index}`}
                          className="pj-star-dust-dot"
                          cx={CX + Math.cos(angle) * radius}
                          cy={CY + Math.sin(angle) * radius}
                          r={index % 4 === 0 ? 1.6 : 1.1}
                          fill={index % 5 === 0 ? 'var(--pj-accent, #F97316)' : 'var(--pj-ink-3, #A8A29E)'}
                          style={{ animationDelay: `${-index * 0.48}s` }}
                        />
                      );
                    })}
                  </g>

                  {/* 三条同心轨道与六条放射轴 */}
                  <circle
                    cx={CX}
                    cy={CY}
                    r={INNER_ORBIT_R}
                    fill="none"
                    stroke={roomCompareMode ? 'var(--pj-border, #FDBA74)' : 'var(--pj-line-strong, #D6D3D1)'}
                    strokeWidth={0.9}
                    strokeDasharray="3 3"
                    opacity={0.7}
                    pointerEvents="none"
                  />
                  <circle cx={CX} cy={CY} r={OUTER_ORBIT_R} fill="none" stroke="var(--pj-line, #E7E5E4)" strokeWidth={0.8} opacity={0.8} pointerEvents="none" />
                  <OrbitComet radius={OUTER_ORBIT_R} />
                  <OrbitComet radius={INNER_ORBIT_R} inner />
                  {Array.from({ length: 6 }, (_, index) => {
                    const angle = ((index * 60) - 90) * Math.PI / 180;
                    return (
                      <line
                        key={`axis-${index}`}
                        x1={CX + Math.cos(angle) * (SELF_R + 7)}
                        y1={CY + Math.sin(angle) * (SELF_R + 7)}
                        x2={CX + Math.cos(angle) * OUTER_ORBIT_R}
                        y2={CY + Math.sin(angle) * OUTER_ORBIT_R}
                        stroke="var(--pj-line-strong, #D6D3D1)"
                        strokeWidth={0.7}
                        opacity={0.34}
                      />
                    );
                  })}

                  {/* 星座连线与极低透明主题面 */}
                  {placed.length > 2 && (
                    <>
                      <polygon
                        className="pj-star-web"
                        points={placed.map((item) => `${item.x},${item.y}`).join(' ')}
                        fill="var(--pj-accent, #F97316)"
                        fillOpacity={0.055}
                        stroke="var(--pj-accent, #F97316)"
                        strokeWidth={1}
                        strokeOpacity={0.38}
                        strokeLinejoin="round"
                        pointerEvents="none"
                      />
                      {placed.map((item, index) => {
                        const next = placed[(index + 1) % placed.length];
                        return (
                          <line
                            className="pj-star-web-line"
                            key={`web-${item.node.id}`}
                            x1={item.x}
                            y1={item.y}
                            x2={next.x}
                            y2={next.y}
                            stroke="var(--pj-accent, #F97316)"
                            strokeWidth={0.8}
                            strokeOpacity={0.28}
                          />
                        );
                      })}
                    </>
                  )}

                  {/* 中心节点：单人「你」与房间共识双态 */}
                  <g pointerEvents="none">
                    <circle className="pj-star-self-ring" cx={CX} cy={CY} r={SELF_R + 9} fill="none" stroke="var(--pj-accent, #F97316)" strokeWidth={0.9} strokeDasharray={roomCompareMode ? '6 4' : '2 5'} opacity={0.45} />
                    <circle cx={CX} cy={CY} r={SELF_R} fill="var(--pj-soft, #F8ECEF)" stroke="var(--pj-border, #ECD6DC)" strokeWidth={1} />
                    <path
                      d={`M ${CX} ${CY - 13} L ${CX + 3.8} ${CY - 3.8} L ${CX + 13} ${CY} L ${CX + 3.8} ${CY + 3.8} L ${CX} ${CY + 13} L ${CX - 3.8} ${CY + 3.8} L ${CX - 13} ${CY} L ${CX - 3.8} ${CY - 3.8} Z`}
                      fill="var(--pj-accent, #F97316)"
                      opacity={0.9}
                    />
                    <text
                      x={CX}
                      y={CY + 26}
                      textAnchor="middle"
                      fontSize={12.5}
                      fontWeight={600}
                      fill="var(--pj-ink-2, #57534E)"
                      letterSpacing={0.6}
                    >
                      {roomCompareMode ? `${activeRoomCode} 共识` : '你'}
                    </text>
                  </g>

                  {active && activeOffset && (
                    <g aria-hidden="true" pointerEvents="none">
                      <line className="pj-star-focus-connection" x1={CX} y1={CY} x2={active.x + activeOffset.x} y2={active.y + activeOffset.y} />
                      <line className="pj-star-focus-signal" pathLength="100" x1={CX} y1={CY} x2={active.x + activeOffset.x} y2={active.y + activeOffset.y} />
                    </g>
                  )}

                  {/* 发光星子节点层 */}
                  {placed.map((item, index) => {
                    const tone = TONE[item.tone];
                    const isFocus = focusId === item.node.id;
                    const isSelected = selectedId === item.node.id;
                    const highlightInRoom = roomCompareMode && item.isShared;
                    const dimmed =
                      (focusId !== '' && !isFocus) || (roomCompareMode && !item.isShared && !isFocus);
                    const strength = weightFor(item.node);
                    const offset = nodeOffset(item);
                    const radius = 6 + strength * 5.5;
                    const labelDist = radius + 12;
                    const dx = item.x - CX;
                    const dy = item.y - CY;
                    const dist = Math.hypot(dx, dy) || 1;
                    const labelX = item.x + (dx / dist) * labelDist;
                    const labelY = item.y + (dy / dist) * labelDist + 3;
                    const labelAnchor = Math.abs(dx) < 1 ? 'middle' : dx > 0 ? 'start' : 'end';

                    return (
                      <g
                        key={`node-${item.node.id}`}
                        className="pj-star-node"
                        style={{
                          opacity: dimmed ? 0.45 : 1,
                          transform: `translate(${offset.x}px, ${offset.y}px)`,
                        }}
                        data-testid={`trait-node-${item.node.kind}`}
                        onMouseEnter={() => setHoverId(item.node.id)}
                        onMouseLeave={() => setHoverId('')}
                        onFocus={() => setHoverId(item.node.id)}
                        onBlur={() => setHoverId('')}
                        onClick={(event) => {
                          event.stopPropagation();
                          selectNode(item.node.id, true);
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
                        <rect
                          x={item.x - 34}
                          y={item.y - 28}
                          width={68}
                          height={56}
                          fill="transparent"
                          pointerEvents="all"
                        />

                        {/* 选中脉冲虚线环 */}
                        {(isFocus || isSelected || highlightInRoom) && (
                          <circle
                            className="pj-star-pulse"
                            cx={item.x}
                            cy={item.y}
                            r={radius + 7}
                            fill="none"
                            stroke={highlightInRoom ? '#F59E0B' : 'var(--pj-accent, #F97316)'}
                            strokeWidth={1.2}
                            strokeDasharray="3 3"
                          />
                        )}

                        {/* 外圈柔和光晕 */}
                        <circle
                          className="pj-star-node-halo"
                          cx={item.x}
                          cy={item.y}
                          r={radius + (isFocus || isSelected ? 6 : 4)}
                          fill={highlightInRoom ? '#F59E0B' : tone.stroke}
                          opacity={isFocus || isSelected || highlightInRoom ? 0.22 : 0.14}
                          style={{ animationDelay: `${(index % 7) * 0.42}s` }}
                        />

                        {/* 四芒星节点 */}
                        <path
                          d={`M ${item.x} ${item.y - radius}
                              L ${item.x + radius * 0.32} ${item.y - radius * 0.32}
                              L ${item.x + radius} ${item.y}
                              L ${item.x + radius * 0.32} ${item.y + radius * 0.32}
                              L ${item.x} ${item.y + radius}
                              L ${item.x - radius * 0.32} ${item.y + radius * 0.32}
                              L ${item.x - radius} ${item.y}
                              L ${item.x - radius * 0.32} ${item.y - radius * 0.32} Z`}
                          fill={highlightInRoom ? '#F59E0B' : tone.stroke}
                          fillOpacity={isFocus || isSelected ? 0.95 : 0.8}
                          stroke="#FFFFFF"
                          strokeWidth={0.8}
                        />
                        <circle cx={item.x} cy={item.y} r={1.6} fill="#FFFFFF" fillOpacity={0.92} />

                        {/* 节点标签 */}
                        <text
                          x={labelX}
                          y={labelY}
                          textAnchor={labelAnchor}
                          fontSize={17}
                          fontWeight={isFocus || isSelected ? 700 : 600}
                          fill={isFocus || isSelected ? 'var(--pj-accent-ink)' : 'var(--pj-ink, #1F2328)'}
                          fontFamily="var(--pj-sans, 'Noto Sans SC', sans-serif)"
                          paintOrder="stroke"
                          stroke="#FFFFFF"
                          strokeWidth={2.2}
                          strokeOpacity={0.92}
                        >
                          {item.label}
                        </text>

                        {/* 房间交集金色共识角标 */}
                        {highlightInRoom && (
                          <text
                            x={item.x}
                            y={item.y - radius - 8}
                            textAnchor="middle"
                            fontSize={9.5}
                            fontWeight={800}
                            fill="#B45309"
                          >
                            ★ 共识
                          </text>
                        )}
                      </g>
                    );
                  })}
                </g>
                </svg>
              </div>

              {/* 画布悬浮底栏：图例 + 筛选 + 缩放 */}
              <div className="pj-star-bottom">
                <div className="pj-star-legend">
                  <span className="pj-star-legend-item"><i className="pj-star-legend-core" />内环：核心维度与预算</span>
                  <span className="pj-star-legend-item"><i className="pj-star-legend-tag" />外环：标签与行为印记</span>
                  {roomCompareMode && (
                    <span className="pj-star-legend-room">★ 金色双环 = 同行好友共同偏好</span>
                  )}
                  {layout.hidden > 0 && <span className="pj-star-legend-hidden">另折叠 {layout.hidden} 项</span>}
                  <span className="pj-star-swipe-hint">左右滑动查看完整星图</span>
                </div>

                <div className="pj-star-footer-tools">
                  {nodes.length > 0 && (
                    <div className="pj-star-filter" role="group" aria-label="星图筛选">
                      <button
                        type="button"
                        aria-label={`全部 ${nodes.length}`}
                        className={filter === 'all' ? 'is-active' : ''}
                        aria-pressed={filter === 'all'}
                        onClick={() => switchFilter('all')}
                      >
                        全部<span>{nodes.length}</span>
                      </button>
                      {FILTERS.map(([value, label]) => {
                        const count = value ? counts[value] || 0 : nodes.length;
                        if (!value || count === 0) return null;
                        return (
                          <button
                            key={value}
                            type="button"
                            aria-label={`${value === 'tag' ? '标签' : label} ${count}`}
                            className={filter === value ? 'is-active' : ''}
                            aria-pressed={filter === value}
                            onClick={() => switchFilter(value)}
                          >
                            {label}<span>{count}</span>
                          </button>
                        );
                      })}
                    </div>
                  )}

                  <span className="pj-star-tool-divider" aria-hidden="true" />
                  <div className="pj-star-zoom">
                    <button type="button" onClick={zoomIn} aria-label="放大星图" title="放大">
                      <ZoomIn className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" className="pj-star-zoom-value" onClick={() => setView(DEFAULT_VIEW)} title="重置缩放">
                      {Math.round(view.scale * 100)}%
                    </button>
                    <button type="button" onClick={zoomOut} aria-label="缩小星图" title="缩小">
                      <ZoomOut className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" onClick={() => setView(DEFAULT_VIEW)} aria-label="重置视角" title="重置视角">
                      <RotateCcw className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* 右侧：Bento 风格洞察、权重微调与节点剔除面板 */}
            <aside
              className="pj-star-inspector"
              ref={inspectorRef}
              tabIndex={-1}
              aria-label="旅行偏好详情"
              data-testid="trait-graph-inspector"
            >
              {active ? (
                <div className="pj-star-inspector-active">
                  <div className="pj-star-inspector-top">
                    <span className="pj-star-node-kind">{KIND_META[active.node.kind].label}</span>
                    {active.isShared && <span className="pj-star-node-shared">★ 房间共识</span>}
                    <button
                      type="button"
                      onClick={resetSelection}
                      className="pj-star-inspector-close"
                      aria-label="关闭节点详情"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>

                  <div className="pj-star-inspector-title">
                    <Star className="h-4 w-4" />
                    <h3>{active.node.label}</h3>
                  </div>

                  <p className="pj-star-inspector-desc">
                    {active.edge?.note ||
                      `越看重「${active.node.label}」，规划时就越优先考虑与之相符的安排。`}
                  </p>

                  {selected ? <div className="pj-star-weight">
                    <div className="pj-star-weight-head">
                      <span><Sliders className="h-3.5 w-3.5" />重视程度</span>
                      <strong>{activeWeightPct}<small> / 100</small></strong>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={1}
                      value={activeWeightPct}
                      disabled={savingWeight}
                      onChange={event => {
                        setWeightDraft({ id: selected.node.id, value: Number(event.target.value) / 100 });
                        setSaveNote(null);
                      }}
                      className="pj-star-range"
                      aria-label={`${active.node.label}的重视程度`}
                    />
                    <div className="pj-star-weight-scale">
                      <span>偶尔考虑</span>
                      <span>优先考虑</span>
                    </div>
                    <div className="pj-star-save-row">
                      <button type="button" onClick={saveWeight} disabled={savingWeight || weightDraft?.id !== selected.node.id || weightDraft.value === displayedWeight(selected.node.weight)}>{savingWeight ? '保存中…' : '保存调整'}</button>
                      {saveNote?.id === selected.node.id && <span role="status">{saveNote.text}</span>}
                    </div>
                  </div> : <p className="pj-star-empty-hint">点选这颗星可固定详情并调整偏好。</p>}

                  <div className="pj-star-inspector-meta">
                    <span>数据来源 · {SOURCE_META[active.node.source] || active.node.source}</span>
                    <span>
                      {active.node.evidence_count
                        ? `累计印记 ${active.node.evidence_count} 次`
                        : '偏好设置主动勾选'}
                    </span>
                  </div>

                  {selected && <button
                    type="button"
                    onClick={() => handleRemoveActiveNode(active.node)}
                    disabled={savingWeight}
                    className="pj-star-remove"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    移出过时标签
                  </button>}
                </div>
              ) : (
                <div className="pj-star-inspector-empty">
                  {roomCompareMode ? (
                    <div className="pj-star-room-summary">
                      <div className="pj-star-room-summary-head">
                        <span><Users className="h-3.5 w-3.5" />房间 {activeRoomCode} 共识</span>
                        <strong>
                          {Math.round((sharedCount / nodes.length) * 100)}%
                        </strong>
                      </div>
                      <p>已高亮你与同行伙伴重合的兴趣锚点。</p>
                      <div className="pj-star-room-tags">
                        {sharedPlaced.slice(0, 6).map((item) => (
                          <button
                            key={item.node.id}
                            type="button"
                            onClick={() => selectNode(item.node.id)}
                          >
                            ★ {item.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <>
                      <span className="pj-star-empty-icon"><Compass className="h-6 w-6" /></span>
                      <p>点选一颗星，查看这项偏好从哪里来。</p>
                      <span className="pj-star-empty-hint">调整重视程度时，可以先看星点怎么移动，再保存。</span>
                    </>
                  )}
                </div>
              )}
            </aside>

          </div>
        </>
      )}
    </section>
  );
}
