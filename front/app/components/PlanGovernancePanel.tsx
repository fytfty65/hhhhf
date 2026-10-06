'use client';

/**
 * PlanGovernancePanel — 预算核对 / 已作调整 / 行程规模 的三段式说明面板。
 *
 * 设计取向（刻意"去 AI 化"）：
 * - 报表与批注的排印语言：细分割线、等宽数字（tabular-nums）、小字号标签，而不是渐变卡片、
 *   发光描边、"✨ 智能推荐"这类措辞；
 * - 只用既有色板（slate / orange / emerald / amber / rose），不引入紫蓝科技渐变；
 * - 文案说人话："已核实 ¥x""可能超支 ¥y""把 A 换成 B，省了 ¥z"，不写"AI 已为你优化"。
 *
 * 数据全部来自后端 `final_route.payload` 的 quality / budget_report / fallback / horizon；
 * 缺字段时**不渲染**（老数据、mock 数据都不会出现空壳）。
 */

import React from 'react';
import { ReceiptText, SlidersHorizontal, CalendarRange, Route, Target, AlertTriangle, BedDouble, Bookmark, MapPin, Scale, Users } from 'lucide-react';

type GateFailure = { code?: string; detail?: string };
type Substitution = { from?: string; node?: string; to?: string; saving?: number | null; reason?: string; kind?: string };
type Segment = { start?: number; end?: number; days?: number };
type TransportLeg = {
  from?: string;
  to?: string;
  distance_km?: number;
  after_day?: number;
  before_day?: number;
  advice?: string;
  alternatives?: string[];
  unverified_fares?: { mode?: string; code?: string; reason?: string }[];
  options?: { mode?: string; duration_minutes?: number; transfers?: number | null; price?: number | null; fare_verified?: boolean }[];
};
type LongTripSegment = {
  index?: number;
  start_day?: number;
  end_day?: number;
  days?: number;
  nodes?: number;
  rest_days?: number;
  needs_repair?: boolean;
};

export type PlanGovernance = {
  dailyBalance?: {
    [day: string]: { daytime?: number; hotel?: number; target_daytime?: number; satisfied?: boolean };
  };
  quality?: {
    score?: number;
    verdict?: string;
    gate_passed?: boolean;
    gate_failures?: GateFailure[];
    unverifiable_count?: number;
  };
  budget?: {
    budget?: number;
    verified_cost?: number;
    estimated_cost?: number;
    unknown_count?: number;
    status?: string;
    shortfall?: number;
    confidence?: string;
  };
  fallback?: {
    trigger?: string;
    shortfall?: number;
    actions?: Substitution[];
    substitutions?: Substitution[];
    dropped?: string[];
    preserved_ratio?: number;
    needs_confirmation?: boolean;
    disclosure?: string;
  };
  horizon?: {
    days?: number;
    segments?: Segment[];
    advisories?: string[];
  };
  /** 二次增量的执行结果（达成了几项、对其它安排扰动多大） */
  increment?: {
    requested?: number;
    achieved?: number;
    target_gain?: number | null;
    disturbance?: number;
    responsiveness?: number | null;
    disclosure?: string;
  };
  /** 价格覆盖情况（可核实比例 / 还需补价的节点） */
  priceAudit?: {
    total?: number;
    verified_ratio?: number;
    unknown_ratio?: number;
    unknown?: string[];
    still_unknown?: string[];
    updated?: number;
    summary?: string;
    /** 需要你确认的清单（后端给出原因 + 可点开的核价入口） */
    pending?: { name?: string; kind?: string; reason?: string; url?: string }[];
    pending_summary?: string;
  };
  /** 住哪更划算：房价之外的连锁影响（通勤 / 交通费 / 体力 / 碳排 / 可玩节点 / 预算余额） */
  lodgingJoint?: {
    status?: string;
    reason?: string;
    note?: string;
    error?: string;
    tradeoffs?: string[];
    /** 被淘汰的住处 → 淘汰它的那些住处（后端 rank_lodging_options 给出） */
    dominated?: Record<string, string[]>;
    candidates?: {
      lodging_name?: string;
      nights?: number;
      lodging_total?: number | null;
      lodging_price_unknown?: boolean;
      commute_minutes_total?: number;
      commute_cost?: number;
      commute_time_value?: number;
      switch_penalty?: number;
      total_cost_known?: number;
      unknown_components?: string[];
      playable_nodes?: number;
      fatigue_index?: number;
      carbon_kg?: number;
      carbon_source?: string;
      budget_remaining?: number | null;
      estimated?: boolean;
    }[];
  };
  /** 本次是否按"参考路线画像"排的（含依据；后端只在诉求没说明白时才用） */
  referenceProfile?: {
    applied?: boolean;
    reference_count?: number;
    reasons?: string[];
    disclosure?: string;
  };
  /** 跨城腿的出行建议（含"票价未核实"清单） */
  transportAudit?: { legs?: TransportLeg[]; note?: string; error?: string };
  /** 用户诉求逐条核对（后端真值字段）：每条诉求一个稳定 id + 状态，前端逐条渲染 */
  constraints?: {
    items?: {
      id?: string;
      category?: string;
      text?: string;
      polarity?: string;
      source?: string;
      status?: string;
      detail?: string;
    }[];
    total?: number;
    counts?: Record<string, number>;
    summary?: string;
    unverified_ids?: string[];
    missing_ids?: string[];
    error?: string;
  };
  /**
   * 节点级负反馈的落地情况：你点过"不喜欢"的地点/写下的原因，这一次到底改了没有。
   *
   * 为什么必须渲染：后端把"已按你的反馈移出「胡杨书院」、并改为就近安排"算出来了，
   * 但界面上一个字都没有 —— 用户会以为点「不喜欢」没用（**没渲染出来的"已按反馈修正"等于没做**）。
   */
  feedbackApplied?: {
    exclude_names?: string[];
    distance_tolerance?: string;
    budget_tendency?: string | null;
    notes?: string[];
    counts?: { negative_total?: number; excluded?: number; distance?: number; price?: number };
  };
  /**
   * 住宿候选的**供应商实价**清单：高德不给房价，所以"住哪"以前没有价格依据。
   * 这份清单是用户的选择权（"同样的预算还有哪些有实价的可选"），必须能看见。
   */
  lodgingSupply?: {
    status?: string;
    error?: string;
    count?: number;
    with_price?: number;
    added?: number;
    nightly_cap?: number | null;
    min_star?: number | null;
    hotels?: { name?: string; price?: number | null; star?: number | null; address?: string; booking_url?: string; message?: string }[];
  };
  /** 数据降级登记：哪个数据源没取到、用户损失了什么（文案由后端注册表给出） */
  degradations?: {
    items?: { source?: string; label?: string; impact?: string; status?: string; reason?: string }[];
    total?: number;
    counts?: Record<string, number>;
    sources?: string[];
    summary?: string;
    error?: string;
  };
  /**
   * 候选供给：这次方案里有多少地点真的来自数据源。
   * 为什么必须显示：数据源限流/覆盖不足时真实候选会很少，剩下的是模型知识补的 ——
   * 用户有权知道自己看到的是"查到的"还是"想出来的"。
   */
  candidateSupply?: {
    status?: string;
    needed_daytime?: number;
    known_daytime?: number;
    attractions?: number;
    foods?: number;
    scenic?: number | null;
    widened?: string[];
    notes?: string[];
    /** 周边县市景区（离市区较远，方案里要预留出行时间） */
    out_of_town_scenic?: number;
    farthest_scenic_km?: number;
  };
  /**
   * 协商纪要（多智能体协同要看得见）：谁参与、站在什么立场、争了什么、裁决凭什么、
   * 谁没参与（含原因）。没有这一块，用户在界面上只能看到结果，看不到协商过程。
   */
  negotiation?: {
    facts_version?: string;
    agents?: { agent_id?: string; domain?: string; prefers?: string; candidates?: number; status?: string }[];
    inactive?: { agent_id?: string; reason?: string }[];
    conflicts?: { code?: string; between?: string[]; detail?: string; severity?: string }[];
    arbitration?: { code?: string; decision?: string; mechanism?: string; why?: string }[];
    /** 协商回环的结果：分歧解掉没、谁按裁决重提了什么（"协商"而不是"裁断"的证据） */
    revision?: {
      round?: number;
      revised_agents?: string[];
      conflicts_before?: string[];
      conflicts_after?: string[];
      resolved?: string[];
      changed?: { agent_id?: string; applied?: Record<string, { limit?: number; dropped_count?: number; dropped?: string[] }>; candidates_after?: number }[];
      notes?: string[];
      /** 为什么停在这里（收敛判断）：已解决 / 达到轮次上限 / 重提无进展 */
      convergence?: { code?: string; reason?: string };
      error?: string;
    };
    notes?: string[];
  };
  /**
   * 行程配比（用户口径）：默认自然景区与人文各半并穿插特色美食/住宿；明确说了偏好就加权。
   * 自然景观只算景区 —— 城市公园单列，不顶自然的名额。
   */
  composition?: {
    scenic?: number;
    cultural?: number;
    food?: number;
    hotel?: number;
    daytime?: number;
    source?: string;
    actual?: {
      scenic?: number;
      landmark?: number;
      city_park?: number;
      cultural?: number;
      food?: number;
      hotel?: number;
      shopping?: number;
      other?: number;
    };
    audit?: { ok?: boolean; notes?: string[]; deviations?: { intent?: string; target?: number; actual?: number; severity?: string }[] };
    /** 按候选供给压过的"做得到的目标"（达不到原目标时，原因写在 supply_limited 里） */
    feasible_targets?: Record<string, number>;
    supply_limited?: Record<string, { target?: number; available?: number }>;
    supply_counts?: Record<string, number>;
    recomputed_after_closeout?: boolean;
  };
  /**
   * 统一事实记录（闭环第①段）：请求/约束/供应商/决策/结果/缺口，
   * 每条事实都带 `source`（谁说的）与 `verified`（能不能当真）。
   * 专业诊断层用它回答"大家看的是不是同一份事实"。
   */
  facts?: {
    schema_version?: number;
    facts_version?: string;
    request?: Record<string, { value?: unknown; source?: string; verified?: boolean; note?: string }>;
    constraints?: Record<string, { value?: unknown; source?: string; verified?: boolean; note?: string }>;
    suppliers?: Record<string, { value?: unknown; source?: string; verified?: boolean; note?: string }>;
    decisions?: Record<string, { value?: unknown; source?: string; verified?: boolean; note?: string }>;
    outcome?: Record<string, { value?: unknown; source?: string; verified?: boolean; note?: string }>;
    gaps?: { area?: string; status?: string; reason?: string }[];
  };
  /** 自动复核（critic + 确定性修补）的结果：改了什么、还剩几处门禁问题、缺哪些数据 */
  review?: {
    stopped_reason?: string;
    rounds?: number;
    action_count?: number;
    actions?: {
      code?: string;
      day?: number;
      node?: string;
      from?: string;
      to?: string;
      dropped?: string[];
      days?: number[];
      count?: number;
    }[];
    initial_score?: number;
    final_score?: number;
    initial_hard_failures?: number;
    remaining_hard?: number;
    needs_data?: string[];
    error?: string;
  };
  /** 长途分段摘要（哪段被重生成过、哪段还没补上） */
  longTrip?: {
    segments?: LongTripSegment[];
    ok?: boolean;
    /** 首轮按 7 天一段生成再合并的结果：排好了几段、哪几段没生成出来 */
    first_round?: {
      chunk_days?: number;
      generated?: number[];
      failed?: number[];
      skipped?: number[];
      truncated?: boolean;
      out_of_range_days?: number[];
    };
    repaired?: { segment?: number; days?: string; added?: number; dropped_out_of_range?: number[]; reasons?: string[] }[];
    repair_failed_segments?: number[];
    error?: string;
  };
};

const BUDGET_STATUS: Record<string, { label: string; tone: string }> = {
  over: { label: '超出预算', tone: 'text-rose-600' },
  at_risk: { label: '可能超支', tone: 'text-amber-600' },
  unverifiable: { label: '价格未核实，暂无法判定', tone: 'text-amber-600' },
  no_budget: { label: '未设置预算', tone: 'text-slate-500' },
  ok: { label: '在预算内', tone: 'text-emerald-600' },
};

// 诉求核对：每条诉求的状态 → 人话标签与颜色（未核实绝不写成"已满足"）
const CONSTRAINT_STATUS: Record<string, { label: string; tone: string }> = {
  applied: { label: '已落实', tone: 'text-emerald-600' },
  partially_applied: { label: '部分落实', tone: 'text-amber-600' },
  unverified: { label: '未核实', tone: 'text-amber-600' },
  advisory: { label: '仅写进偏好', tone: 'text-slate-500' },
  missing: { label: '没做到', tone: 'text-rose-600' },
};

// 自动复核做了什么 → 人话标签
const REVIEW_ACTION_LABELS: Record<string, string> = {
  reschedule: '重排时间',
  dedupe: '去掉重复',
  drop_out_of_range: '丢弃越界天数',
  trim_day: '单日限流',
  fill_day: '补空天',
  add_play: '补玩点',
  add_meal: '补餐饮',
  add_lodging: '补住宿',
  add_must_have: '补必去项',
};

// 复核改不动、需要补数据的部分（不编造，只能去数据源取或问用户）
const REVIEW_NEEDS_DATA_LABELS: Record<string, string> = {
  needs_candidates: '候选池补点（缺玩点 / 餐 / 住宿）',
  needs_data: '补数据（价格 / 开放时间）',
};

function summarizeReviewActions(actions: { code?: string }[]): string {
  const counts = new Map<string, number>();
  for (const action of actions) {
    const key = action.code || 'other';
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([code, count]) => `${REVIEW_ACTION_LABELS[code] || '调整'} ${count} 处`)
    .join(' · ');
}

// 门禁失败码 → 人话标签（说"哪里不合适"，不说"AI 检测到异常"）
const GATE_LABELS: Record<string, string> = {
  budget_exceeded: '预算超支',
  budget_at_risk: '预算可能超支',
  budget_partial: '部分价格未核实',
  budget_unverifiable: '价格未核实',
  day_empty: '某一天没有安排',
  day_too_thin: '某一天安排过少',
  day_out_of_order: '同一天时间顺序颠倒',
  time_out_of_window: '时间超出可用时段',
  time_missing: '缺少时间',
  must_have_missing: '缺少你要求的内容',
  opening_hours_conflict: '营业时间对不上',
  open_time_missing: '营业时间未核实',
  skeleton_lodging_shortfall: '住宿夜数不够',
  skeleton_meal_shortfall: '餐饮安排不足',
  skeleton_meal_missing: '某一天没有安排吃饭',
  skeleton_play_missing: '某一天没有游玩安排',
};

function money(value: unknown): string {
  const num = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(num)) return '—';
  return `¥${Math.round(num).toLocaleString()}`;
}

function SectionLabel({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px] font-bold tracking-wide text-slate-500">
      {icon}
      <span>{children}</span>
    </div>
  );
}

function Row({ label, value, tone, hint }: { label: string; value: React.ReactNode; tone?: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-[12.5px] text-slate-600" title={hint}>{label}</span>
      <span className={`font-mono text-[13.5px] font-bold tabular-nums ${tone || 'text-slate-800'}`}>{value}</span>
    </div>
  );
}

export default function PlanGovernancePanel({ data }: { data?: PlanGovernance | null }) {
  if (!data) return null;
  const { quality, budget, fallback, horizon, dailyBalance, increment, priceAudit, transportAudit, longTrip, review, constraints, degradations, lodgingJoint, referenceProfile, candidateSupply, composition, negotiation, feedbackApplied, lodgingSupply } = data;

  const budgetStatus = budget?.status ? BUDGET_STATUS[budget.status] : undefined;
  const failures = (quality?.gate_failures || []).filter((item) => item && item.code);
  const advisories = horizon?.advisories || [];
  const substitutions = (fallback?.substitutions || fallback?.actions || []).filter((item) => item && (item.to || item.reason));
  const segments = horizon?.segments || [];

  const hasBudgetBlock = Boolean(
    budget && ((budget.budget ?? 0) > 0 || (budget.verified_cost ?? 0) > 0 || (budget.estimated_cost ?? 0) > 0),
  );
  const hasFallbackBlock = Boolean(fallback && (fallback.disclosure || substitutions.length));
  const hasHorizonBlock = advisories.length > 0;
  const hasFailureBlock = failures.length > 0;
  const hasIncrementBlock = Boolean(increment && (increment.disclosure || (increment.requested ?? 0) > 0));
  const hasPriceBlock = Boolean(
    priceAudit && ((priceAudit.total ?? 0) > 0 || priceAudit.summary || (priceAudit.unknown?.length ?? 0) > 0),
  );
  const hasTransportBlock = Boolean(transportAudit && (transportAudit.legs?.length || transportAudit.error));
  const hasLongTripBlock = Boolean(longTrip && (longTrip.segments?.length || longTrip.error));
  const hasReviewBlock = Boolean(
    review &&
      (review.error ||
        (review.action_count ?? 0) > 0 ||
        (review.initial_hard_failures ?? 0) > (review.remaining_hard ?? 0)),
  );
  const hasConstraintBlock = Boolean(constraints && ((constraints.items?.length ?? 0) > 0 || constraints.error));
  // 反馈落地：后端给了"排除了谁/改成了什么口径"才显示（缺字段不渲染空壳）
  const hasFeedbackBlock = Boolean(
    feedbackApplied &&
      ((feedbackApplied.exclude_names?.length ?? 0) > 0 || (feedbackApplied.notes?.length ?? 0) > 0),
  );
  // 住宿实价候选：有清单才显示（没查到就不显示空壳，但错误要显示出来）
  const hasLodgingSupplyBlock = Boolean(
    lodgingSupply && ((lodgingSupply.hotels?.length ?? 0) > 0 || lodgingSupply.error),
  );
  const hasDegradationBlock = Boolean(
    degradations && ((degradations.items?.length ?? 0) > 0 || degradations.error),
  );
  const hasLodgingJointBlock = Boolean(
    lodgingJoint &&
      ((lodgingJoint.candidates?.length ?? 0) > 0 || lodgingJoint.reason || lodgingJoint.error || lodgingJoint.note),
  );
  const hasReferenceProfileBlock = Boolean(
    referenceProfile && (referenceProfile.disclosure || (referenceProfile.reasons?.length ?? 0) > 0),
  );
  // 供给信息只在后端真的报了数字时显示（缺字段不渲染空壳）
  const hasCandidateSupplyBlock = Boolean(
    candidateSupply && (typeof candidateSupply.known_daytime === 'number' || candidateSupply.status),
  );
  // 协商纪要：后端给了智能体列表或分歧/裁决就显示
  const hasNegotiationBlock = Boolean(
    negotiation &&
      ((negotiation.agents?.length ?? 0) > 0 ||
        (negotiation.conflicts?.length ?? 0) > 0 ||
        (negotiation.inactive?.length ?? 0) > 0),
  );
  // 配比：后端给了目标就算有（actual/audit 可能缺，缺了就不显示对账那半句）
  const hasCompositionBlock = Boolean(
    composition && (typeof composition.scenic === 'number' || typeof composition.daytime === 'number'),
  );
  if (
    !hasBudgetBlock && !hasFallbackBlock && !hasHorizonBlock && !hasFailureBlock &&
    !hasIncrementBlock && !hasPriceBlock && !hasTransportBlock && !hasLongTripBlock &&
    !hasReviewBlock && !hasConstraintBlock && !hasDegradationBlock && !hasLodgingJointBlock &&
    !hasReferenceProfileBlock && !hasCandidateSupplyBlock && !hasCompositionBlock && !hasNegotiationBlock &&
    !hasFeedbackBlock && !hasLodgingSupplyBlock
  ) {
    return null;
  }

  return (
    <section
      className="mt-3 rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3.5 text-left"
      aria-label="行程核对说明"
    >
      {hasCompositionBlock && (
        <div
          className="mt-3 border-t border-slate-200 pt-3 first:mt-0 first:border-t-0 first:pt-0"
          data-testid="composition-block"
        >
          <div className="flex items-center justify-between">
            <SectionLabel icon={<Scale className="h-3 w-3" />}>这次的配比</SectionLabel>
            <span
              className={`font-mono text-[12.5px] font-bold tabular-nums ${
                composition?.audit?.ok === false ? 'text-amber-700' : 'text-slate-600'
              }`}
              data-testid="composition-source"
            >
              {composition?.source === 'default_balanced' ? '默认：自然与人文各半' : '按你说的偏好加权'}
            </span>
          </div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-600" data-testid="composition-compare">
            目标：自然景区 {composition?.scenic ?? 0} · 人文 {composition?.cultural ?? 0} · 特色美食{' '}
            {composition?.food ?? 0} · 住宿 {composition?.hotel ?? 0}
            {'　'}
            实际：自然景区 {composition?.actual?.landmark ?? composition?.actual?.scenic ?? 0} · 人文{' '}
            {composition?.actual?.cultural ?? 0} · 特色美食 {composition?.actual?.food ?? 0} · 住宿{' '}
            {composition?.actual?.hotel ?? 0}
          </p>
          <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-400">
            自然景观只算景区（湖/雪山/草原/湿地这类）；城市公园另算 {composition?.actual?.city_park ?? 0} 个，不顶自然的名额。
          </p>
          {/* 目标受候选供给限制时说清楚：达不到原目标不是"没安排"，是这座城市候选里就这么多 */}
          {Object.keys(composition?.supply_limited || {}).length > 0 && (
            <p className="mt-1 text-[11.5px] leading-relaxed text-amber-800" data-testid="composition-supply-limit">
              候选供给限制：
              {Object.entries(composition?.supply_limited || {})
                .map(([key, item]) => {
                  const label: Record<string, string> = { scenic: '自然景区', cultural: '人文', food: '特色美食', hotel: '住宿' };
                  return `${label[key] || key}候选只有 ${item?.available ?? 0} 个（原目标 ${item?.target ?? 0} 个）`;
                })
                .join('；')}
              ，这不是没安排。
            </p>
          )}
          {composition?.audit?.ok === false && (composition?.audit?.notes?.length ?? 0) > 0 && (
            <ul className="mt-1.5 space-y-1" data-testid="composition-notes">
              {(composition?.audit?.notes || []).map((note) => (
                <li key={note} className="text-[12.5px] leading-relaxed text-amber-800">
                  {note}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {hasLodgingSupplyBlock && (
        <div
          className="mt-3 border-t border-slate-200 pt-3 first:mt-0 first:border-t-0 first:pt-0"
          data-testid="lodging-supply-block"
        >
          <div className="flex items-center justify-between">
            <SectionLabel icon={<BedDouble className="h-3 w-3" />}>有实价的住宿可选</SectionLabel>
            {typeof lodgingSupply?.with_price === 'number' && (
              <span className="font-mono text-[12.5px] font-bold tabular-nums text-slate-600">
                {lodgingSupply.with_price} 家有报价
              </span>
            )}
          </div>
          {lodgingSupply?.error ? (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">
              这次没拿到住宿实价候选（{lodgingSupply.error}）——`方案里的住宿仍按"需要你确认"处理。
            </p>
          ) : (
            <>
              <ul className="mt-1.5 space-y-1" data-testid="lodging-supply-list">
                {(lodgingSupply?.hotels || []).slice(0, 4).map((hotel, index) => (
                  <li key={`${hotel.name}-${index}`} className="text-[12.5px] leading-relaxed text-slate-600">
                    <span className="font-bold text-slate-700">{hotel.name}</span>
                    {hotel.price != null ? `：¥${hotel.price}/晚` : '：暂无报价'}
                    {hotel.star != null ? `（${hotel.star} 星）` : ''}
                    {hotel.address ? ` · ${hotel.address}` : ''}
                    {hotel.booking_url ? (
                      <>
                        {' '}
                        <a
                          href={hotel.booking_url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-orange-700 underline decoration-dotted"
                        >
                          打开核对/预订
                        </a>
                      </>
                    ) : null}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500">
                以上是供应商返回的<span className="font-bold text-slate-600">可核实报价</span>（不是估算）
                {lodgingSupply?.nightly_cap ? `，查询时按每晚上限 ¥${lodgingSupply.nightly_cap}` : ''}
                {lodgingSupply?.min_star ? `、${lodgingSupply.min_star} 星以上` : ''}
                ；选哪家由你定，方案里排的那家只是当前推荐。
              </p>
            </>
          )}
        </div>
      )}
      {hasFeedbackBlock && (
        <div
          className="mt-3 border-t border-slate-200 pt-3 first:mt-0 first:border-t-0 first:pt-0"
          data-testid="feedback-applied-block"
        >
          <div className="flex items-center justify-between">
            <SectionLabel icon={<SlidersHorizontal className="h-3 w-3" />}>按你的反馈调整</SectionLabel>
            {(feedbackApplied?.counts?.excluded ?? 0) > 0 && (
              <span className="font-mono text-[12.5px] font-bold tabular-nums text-emerald-700">
                移出 {feedbackApplied?.counts?.excluded} 个
              </span>
            )}
          </div>
          <ul className="mt-1.5 space-y-1">
            {(feedbackApplied?.notes || []).slice(0, 3).map((note, index) => (
              <li key={`feedback-note-${index}`} className="text-[12.5px] leading-relaxed text-slate-600">
                <span className="text-slate-400">·</span>
                <span className="ml-1">{note}</span>
              </li>
            ))}
          </ul>
          {(feedbackApplied?.exclude_names?.length ?? 0) > 0 && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">
              这次不再安排：{(feedbackApplied?.exclude_names || []).join('、')}
              {feedbackApplied?.distance_tolerance === 'low' ? '（并按"就近"收缩当天的距离半径）' : ''}
            </p>
          )}
        </div>
      )}
      {hasNegotiationBlock && (
        <div
          className="mt-3 border-t border-slate-200 pt-3 first:mt-0 first:border-t-0 first:pt-0"
          data-testid="negotiation-block"
        >
          <div className="flex items-center justify-between">
            <SectionLabel icon={<Users className="h-3 w-3" />}>本次协商纪要</SectionLabel>
            <span className="font-mono text-[11px] tabular-nums text-slate-400" data-testid="negotiation-version">
              {negotiation?.facts_version || ''}
            </span>
          </div>

          {/* 参与的智能体：谁站在什么立场（不是"AI 已优化"，而是各自的主张） */}
          {(negotiation?.agents?.length ?? 0) > 0 && (
            <ul className="mt-1.5 space-y-0.5" data-testid="negotiation-stances">
              {(negotiation?.agents || []).map((agent) => (
                <li key={`${agent.agent_id}-${agent.domain}`} className="text-[12.5px] leading-relaxed text-slate-600">
                  · {agent.agent_id}
                  {agent.prefers ? `：${agent.prefers}` : ''}
                  {typeof agent.candidates === 'number' ? `（${agent.candidates} 个候选）` : ''}
                </li>
              ))}
            </ul>
          )}

          {/* 分歧：有就逐条写清楚；没有也明说是"检查过且一致" */}
          {(negotiation?.conflicts?.length ?? 0) > 0 ? (
            <div className="mt-2" data-testid="negotiation-conflicts">
              {(negotiation?.conflicts || []).map((conflict) => (
                <p key={conflict.code} className="text-[12.5px] leading-relaxed text-amber-800">
                  分歧（{conflict.severity === 'hard' ? '硬冲突' : '可协商'}）：
                  {conflict.detail || conflict.code}
                </p>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-[12.5px] leading-relaxed text-slate-500" data-testid="negotiation-no-conflicts">
              {negotiation?.notes?.[0] || '这次各智能体没有显式分歧。'}
            </p>
          )}

          {/* 裁决：决定 + 凭什么 + 由哪一层执行 */}
          {(negotiation?.arbitration?.length ?? 0) > 0 && (
            <ul className="mt-1.5 space-y-1" data-testid="negotiation-arbitration">
              {(negotiation?.arbitration || []).map((item) => (
                <li key={`${item.code}-${item.decision}`} className="text-[12.5px] leading-relaxed text-slate-600">
                  · 裁决：{item.decision}
                  {item.why ? `（${item.why}）` : ''}
                  {item.mechanism ? <span className="text-slate-400">　由 {item.mechanism} 执行</span> : null}
                </li>
              ))}
            </ul>
          )}

          {/* 协商回环：分歧有没有真的被"重提"解决（不是只裁断一句） */}
          {negotiation?.revision && (
            <div className="mt-1.5" data-testid="negotiation-revision">
              <p className="text-[12.5px] leading-relaxed text-slate-600">
                协商第 {negotiation.revision.round ?? 1} 轮：
                {(negotiation.revision.revised_agents?.length ?? 0) > 0
                  ? `${negotiation.revision.revised_agents?.join('、')} 按裁决重提`
                  : '没有智能体需要重提'}
                ；
                {(negotiation.revision.conflicts_before?.length ?? 0) > 0
                  ? `分歧 ${negotiation.revision.conflicts_before?.length} 条 → ${negotiation.revision.conflicts_after?.length ?? 0} 条`
                  : '本轮无分歧'}
                {(negotiation.revision.resolved?.length ?? 0) > 0
                  ? `（已解决：${negotiation.revision.resolved?.join('、')}）`
                  : ''}
                {negotiation.revision.convergence?.reason ? `｜${negotiation.revision.convergence.reason}` : ''}
              </p>
              {(negotiation.revision.changed?.length ?? 0) > 0 && (
                <ul className="mt-0.5 space-y-0.5">
                  {(negotiation.revision.changed || []).map((item) => {
                    const applied = item.applied || {};
                    const parts = Object.entries(applied).map(([key, value]) => {
                      const limit = value?.limit;
                      const dropped = value?.dropped_count ?? value?.dropped?.length ?? 0;
                      return `${key} 上限 ${limit ?? '?'}，去掉 ${dropped} 条`;
                    });
                    return (
                      <li key={item.agent_id} className="text-[12px] leading-relaxed text-slate-500">
                        · {item.agent_id}：{parts.join('；')}
                        {typeof item.candidates_after === 'number' ? `（重提后 ${item.candidates_after} 个候选）` : ''}
                      </li>
                    );
                  })}
                </ul>
              )}
              {negotiation.revision.error && (
                <p className="text-[12px] leading-relaxed text-amber-800">协商重提失败：{negotiation.revision.error}</p>
              )}
            </div>
          )}

          {/* 没参与的：如实列出，不假装协同过 */}
          {(negotiation?.inactive?.length ?? 0) > 0 && (
            <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-400" data-testid="negotiation-inactive">
              这轮没有参与博弈：{(negotiation?.inactive || []).map((item) => `${item.agent_id}（${item.reason}）`).join('；')}
            </p>
          )}
        </div>
      )}
      {hasCandidateSupplyBlock && (
        <div
          className="mt-3 border-t border-slate-200 pt-3 first:mt-0 first:border-t-0 first:pt-0"
          data-testid="candidate-supply-block"
        >
          <div className="flex items-center justify-between">
            <SectionLabel icon={<MapPin className="h-3 w-3" />}>这次的地点有多少是查到的</SectionLabel>
            <span
              className={`font-mono text-[12.5px] font-bold tabular-nums ${
                candidateSupply?.status === 'ok' ? 'text-slate-600' : 'text-amber-700'
              }`}
              data-testid="candidate-supply-counts"
            >
              真实候选 {candidateSupply?.known_daytime ?? 0}
              {typeof candidateSupply?.needed_daytime === 'number' ? ` / 需要 ${candidateSupply.needed_daytime}` : ''}
            </span>
          </div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-600">
            景点 {candidateSupply?.attractions ?? 0} 个
            {typeof candidateSupply?.scenic === 'number' ? `（其中自然景观 ${candidateSupply.scenic} 个）` : ''}
            ，餐饮 {candidateSupply?.foods ?? 0} 个
            {(candidateSupply?.widened?.length ?? 0) > 0 ? `；已额外补检索：${candidateSupply?.widened?.join('、')}` : ''}
          </p>
          {(candidateSupply?.out_of_town_scenic ?? 0) > 0 && (
            <p className="mt-0.5 text-[12px] leading-relaxed text-slate-500" data-testid="candidate-supply-outoftown">
              其中周边县市景区 {candidateSupply?.out_of_town_scenic} 个
              {typeof candidateSupply?.farthest_scenic_km === 'number' && candidateSupply.farthest_scenic_km > 0
                ? `（最远约 ${candidateSupply.farthest_scenic_km} 公里）`
                : ''}
              ，排进去要预留来回时间。
            </p>
          )}
          {(candidateSupply?.notes?.length ?? 0) > 0 && (
            <ul className="mt-1.5 space-y-1">
              {(candidateSupply?.notes || []).map((note) => (
                <li key={note} className="text-[12.5px] leading-relaxed text-amber-800">
                  {note}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {hasReferenceProfileBlock && (
        <div className="mt-3 border-t border-slate-200 pt-3 first:mt-0 first:border-t-0 first:pt-0" data-testid="reference-profile-block">
          <div className="flex items-center justify-between">
            <SectionLabel icon={<Bookmark className="h-3 w-3" />}>这次怎么定的口味</SectionLabel>
            <span className="font-mono text-[12.5px] font-bold tabular-nums text-slate-600">
              按你参考过的 {Math.max(1, referenceProfile?.reference_count ?? 1)} 条路线
            </span>
          </div>
          {(referenceProfile?.reasons?.length ?? 0) > 0 && (
            <ul className="mt-1.5 space-y-1">
              {(referenceProfile?.reasons || []).map((reason, index) => (
                <li key={`${reason}-${index}`} className="text-[12.5px] leading-relaxed text-slate-600">
                  · {reason}
                </li>
              ))}
            </ul>
          )}
          {referenceProfile?.disclosure && (
            <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500">{referenceProfile.disclosure}</p>
          )}
        </div>
      )}

      {hasBudgetBlock && (
        <div>
          <div className="flex items-center justify-between">
            <SectionLabel icon={<ReceiptText className="h-3 w-3" />}>预算核对</SectionLabel>
            <span className={`text-[12.5px] font-bold ${budgetStatus?.tone}`}>{budgetStatus?.label}</span>
          </div>
          <div className="mt-1.5">
            <Row label="已核实花费" value={money(budget?.verified_cost)} hint="来自供应商/可核来源的价格" />
            {(budget?.estimated_cost ?? 0) > 0 && (
              <Row
                label="估算花费（未核实）"
                value={money(budget?.estimated_cost)}
                tone="text-amber-600"
                hint="这部分是估算值，已标明，不计入「已核实」结论"
              />
            )}
            {(budget?.budget ?? 0) > 0 && <Row label="你的预算" value={money(budget?.budget)} tone="text-slate-500" />}
            {(budget?.shortfall ?? 0) > 0 && <Row label="缺口" value={money(budget?.shortfall)} tone="text-rose-600" />}
            {(budget?.unknown_count ?? 0) > 0 && (
              <Row label="未取到价格" value={`${budget?.unknown_count} 个节点`} tone="text-slate-500" />
            )}
          </div>
        </div>
      )}

      {hasFallbackBlock && (
        <div className={hasBudgetBlock ? 'mt-4 border-t border-slate-200 pt-3' : ''}>
          <SectionLabel icon={<SlidersHorizontal className="h-3 w-3" />}>已作调整</SectionLabel>
          {fallback?.disclosure && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-600">{fallback.disclosure}</p>
          )}
          {substitutions.length > 0 && (
            <ul className="mt-1.5 space-y-1">
              {substitutions.slice(0, 5).map((item, index) => {
                const from = item.from || item.node;
                return (
                  <li key={`${from || item.kind}-${index}`} className="flex items-baseline gap-2 text-[12.5px] text-slate-600">
                    <span className="text-slate-500">·</span>
                    {from && item.to ? (
                      <span>
                        把「{from}」换成「{item.to}」
                        {typeof item.saving === 'number' && item.saving > 0 && (
                          <span className="ml-1 font-mono font-bold tabular-nums text-emerald-600">省 {money(item.saving)}</span>
                        )}
                      </span>
                    ) : (
                      <span>{item.reason}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {fallback?.needs_confirmation && (
            <p className="mt-1.5 text-[12.5px] font-bold text-amber-600">
              还有缺口，只能通过改天数或换城市才能补上——这类改动我不会自动做，你确认了我再调整。
            </p>
          )}
        </div>
      )}

      {hasHorizonBlock && (
        <div className={(hasBudgetBlock || hasFallbackBlock) ? 'mt-4 border-t border-slate-200 pt-3' : ''}>
          <div className="flex items-center justify-between">
            <SectionLabel icon={<CalendarRange className="h-3 w-3" />}>行程规模</SectionLabel>
            <span className="font-mono text-[12.5px] font-bold tabular-nums text-slate-600">
              {horizon?.days} 天
              {segments.length > 1 && ` · ${segments.length} 段（${segments.map((s) => s.days).join('/')}）`}
            </span>
          </div>
          <ul className="mt-1.5 space-y-1">
            {advisories.slice(0, 3).map((text, index) => (
              <li key={index} className="flex items-baseline gap-2 text-[12.5px] leading-relaxed text-slate-600">
                <span className="text-slate-500">·</span>
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {dailyBalance && Object.keys(dailyBalance).length > 0 && (
        <div className="mt-3 border-t border-slate-200 pt-2.5">
          <div className="flex items-center justify-between">
            <SectionLabel icon={<CalendarRange className="h-3 w-3" />}>每日安排</SectionLabel>
            <span className="text-[11px] text-slate-500">白天节点，不含住宿</span>
          </div>
          <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1">
            {Object.entries(dailyBalance).map(([day, summary]) => {
              const actual = summary.daytime ?? 0;
              const target = summary.target_daytime ?? 0;
              const satisfied = summary.satisfied ?? (target > 0 ? actual >= target : true);
              return (
                <div key={day} className="flex items-center justify-between gap-2 text-[12px]">
                  <span className="text-slate-600">Day {day}</span>
                  <span className={satisfied ? 'font-medium text-emerald-600' : 'font-medium text-amber-600'}>
                    {actual}/{target || '—'} 个点{satisfied ? '' : ' · 候选不足'}
                  </span>
                </div>
              );
            })}
          </div>
          {Object.values(dailyBalance).some((summary) => summary.satisfied === false) && (
            <p className="mt-1.5 text-[12px] leading-relaxed text-amber-600">
              个别日期的真实候选不足，系统已做跨日均衡；未复制地点，也未编造新的价格或开放时间。
            </p>
          )}
        </div>
      )}

      {hasIncrementBlock && (
        <div className="mt-3 border-t border-slate-200 pt-2.5">
          <div className="flex items-center justify-between">
            <SectionLabel icon={<SlidersHorizontal className="h-3 w-3" />}>本次调整</SectionLabel>
            {typeof increment?.responsiveness === 'number' && (
              <span className="font-mono text-[12.5px] font-bold tabular-nums text-slate-600">
                响应度 {Math.round(increment.responsiveness * 100)}%
              </span>
            )}
          </div>
          {(increment?.requested ?? 0) > 0 && (
            <div className="mt-1.5">
              <Row label="你要的调整" value={`${increment?.requested} 项`} />
              <Row
                label="实际达成"
                value={`${increment?.achieved ?? 0} 项`}
                tone={(increment?.achieved ?? 0) >= (increment?.requested ?? 0) ? 'text-emerald-600' : 'text-amber-600'}
              />
              {typeof increment?.disturbance === 'number' && (
                <Row
                  label="对其余安排的影响"
                  value={`${Math.round(increment.disturbance * 100)}%`}
                  tone="text-slate-500"
                  hint="其余内容被改动的比例，越低说明只动了该动的地方"
                />
              )}
            </div>
          )}
          {increment?.disclosure && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-600">{increment.disclosure}</p>
          )}
        </div>
      )}

      {hasPriceBlock && (
        <div className="mt-4 border-t border-slate-200 pt-3">
          <div className="flex items-center justify-between">
            <SectionLabel icon={<ReceiptText className="h-3 w-3" />}>价格核对</SectionLabel>
            <span className="font-mono text-[12.5px] font-bold tabular-nums text-slate-600">
              {(() => {
                // 人话优先：不写"可核实 27% / 未取到 73%"，直接说"几项已核实、几项要你确认"
                const total = priceAudit?.total ?? 0;
                const verified = Math.round((priceAudit?.verified_ratio ?? 0) * total);
                const unknown = Math.max(
                  priceAudit?.still_unknown?.length || priceAudit?.unknown?.length || 0,
                  Math.round((priceAudit?.unknown_ratio ?? 0) * total),
                );
                return `已核实 ${verified} 项 · 需要你确认 ${unknown} 项`;
              })()}
            </span>
          </div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500">
            价格分三类：<strong className="text-slate-700">已核实</strong>（来自供应商/官方）、
            <strong className="text-slate-700">估算</strong>（已标明，不计入"已核实花费"）、
            <strong className="text-slate-700">需要你确认</strong>（暂时取不到来源）。
            取不到的价格不会当成 0 元。
          </p>
          {(() => {
            const missing = (priceAudit?.still_unknown?.length ? priceAudit?.still_unknown : priceAudit?.unknown) || [];
            return missing.length > 0 ? (
              <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-600">
                还需补价：{missing.slice(0, 5).join('、')}
                {missing.length > 5 ? ` 等 ${missing.length} 个` : ''}
              </p>
            ) : null;
          })()}
          {/* "需要你确认"逐条说清原因，并给一个能点开核价的入口 ——
              只说"未取到 73%"用户没法行动，酒店房价尤其要写明白（高德就是不提供房价）。 */}
          {(priceAudit?.pending?.length ?? 0) > 0 && (
            <div className="mt-2 space-y-1" data-testid="price-pending">
              <p className="text-[12.5px] font-bold text-slate-600">
                {priceAudit?.pending_summary || '以下价格需要你确认：'}
              </p>
              {(priceAudit?.pending || []).slice(0, 5).map((item, index) => (
                <div key={`${item.name}-${index}`} className="flex items-baseline justify-between gap-3">
                  <span className="text-[12.5px] leading-relaxed text-slate-600">
                    <strong className="font-bold text-slate-700">{item.name}</strong>
                    <span className="ml-1 text-slate-500">{item.reason}</span>
                  </span>
                  {item.url && (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="shrink-0 text-[12.5px] font-bold text-orange-600 underline underline-offset-2 hover:text-orange-700"
                    >
                      去核价
                    </a>
                  )}
                </div>
              ))}
            </div>
          )}
          {priceAudit?.summary && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">{priceAudit.summary}</p>
          )}
        </div>
      )}

      {hasLodgingJointBlock && (
        <div className="mt-3 border-t border-slate-200 pt-2.5" data-testid="lodging-joint">
          <div className="flex items-center justify-between">
            <SectionLabel icon={<BedDouble className="h-3 w-3" />}>住哪更划算</SectionLabel>
            <span className="font-mono text-[12.5px] font-bold tabular-nums text-slate-600">
              {lodgingJoint?.status === 'evaluated' ? `比较了 ${lodgingJoint?.candidates?.length ?? 0} 个可选住处` : '这次没有比较'}
            </span>
          </div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500">
            住哪不只看房价：还要看每天从住处往返景点的<strong className="text-slate-700">通勤时间</strong>、
            <strong className="text-slate-700">交通花费</strong>、<strong className="text-slate-700">体力消耗</strong>
            与<strong className="text-slate-700">碳排放</strong>。这几项一起算，各有取舍的住处都留下。
          </p>
          {(lodgingJoint?.candidates?.length ?? 0) > 0 && (
            <div className="mt-2 space-y-1.5">
              {(lodgingJoint?.candidates || []).slice(0, 3).map((item, index) => (
                <div key={`${item.lodging_name}-${index}`} className="rounded-lg border border-slate-200 bg-white px-2.5 py-2">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[12.5px] font-bold text-slate-700">{item.lodging_name || '未命名住处'}</span>
                    <span className="shrink-0 font-mono text-[12.5px] font-bold tabular-nums text-slate-600">
                      {item.lodging_price_unknown
                        ? '房价未取到'
                        : `住 ${item.nights ?? 1} 晚 ¥${Math.round(item.lodging_total || 0)}`}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-slate-500">
                    <span>
                      每天往返 <strong className="tabular-nums text-slate-700">{item.commute_minutes_total ?? 0} 分钟</strong>
                    </span>
                    <span>
                      交通约 ¥<strong className="tabular-nums text-slate-700">{Math.round(item.commute_cost || 0)}</strong>
                    </span>
                    <span>
                      体力负担 <strong className="tabular-nums text-slate-700">{item.fatigue_index ?? 0}</strong>
                    </span>
                    <span>
                      碳排约 <strong className="tabular-nums text-slate-700">{item.carbon_kg ?? 0}</strong> kg
                    </span>
                    {typeof item.budget_remaining === 'number' && (
                      <span>
                        预算余额 ¥<strong className="tabular-nums text-slate-700">{Math.round(item.budget_remaining)}</strong>
                      </span>
                    )}
                  </div>
                  {(item.unknown_components?.length ?? 0) > 0 && (
                    <p className="mt-1 text-[12px] leading-relaxed text-amber-700">
                      未取到：
                      {(item.unknown_components || []).map((code) => (code === 'lodging_price' ? '房价' : code)).join('、')}
                      （不计入总价，也不当成 0 元）
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
          {(lodgingJoint?.tradeoffs?.length ?? 0) > 0 && (
            <ul className="mt-2 space-y-1">
              {(lodgingJoint?.tradeoffs || []).map((line, index) => (
                <li key={index} className="text-[12.5px] leading-relaxed text-slate-600">
                  · {line}
                </li>
              ))}
            </ul>
          )}
          {lodgingJoint?.note && <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500">{lodgingJoint.note}</p>}
          {lodgingJoint?.status === 'skipped' && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-amber-700">
              {lodgingJoint.note || '这次没有做住宿的联合比较，所以不给结论。'}
            </p>
          )}
          {lodgingJoint?.status === 'failed' && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-rose-700">
              住宿的联合比较没算成功（{lodgingJoint.error || '未知原因'}），所以这里不下结论。
            </p>
          )}
        </div>
      )}

      {hasTransportBlock && (
        <div className="mt-3 border-t border-slate-200 pt-2.5">
          <SectionLabel icon={<Route className="h-3 w-3" />}>跨城怎么走</SectionLabel>
          <ul className="mt-1.5 space-y-1.5">
            {(transportAudit?.legs || []).slice(0, 2).map((leg, index) => (
              <li key={`${leg.from}-${leg.to}-${index}`} className="text-[12.5px] leading-relaxed text-slate-600">
                <span className="font-bold text-slate-700">
                  {leg.from} → {leg.to}
                </span>
                {typeof leg.distance_km === 'number' && (
                  <span className="ml-1 font-mono tabular-nums text-slate-500">约 {leg.distance_km} km</span>
                )}
                {leg.advice && <div>{leg.advice}</div>}
                {leg.alternatives && leg.alternatives.length > 0 && (
                  <div className="text-slate-500">备选：{leg.alternatives.slice(0, 2).join('；')}</div>
                )}
              </li>
            ))}
          </ul>
          {transportAudit?.error && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">
              这一段没能比出行方式（{transportAudit.error}），票价与班次未核实。
            </p>
          )}
          {transportAudit?.note && <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">{transportAudit.note}</p>}
        </div>
      )}

      {hasLongTripBlock && (
        <div className="mt-3 border-t border-slate-200 pt-2.5">
          <div className="flex items-center justify-between">
            <SectionLabel icon={<CalendarRange className="h-3 w-3" />}>长途分段</SectionLabel>
            {typeof longTrip?.ok === 'boolean' && (
              <span className={`text-[12.5px] font-bold ${longTrip.ok ? 'text-emerald-600' : 'text-amber-600'}`}>
                {longTrip.ok ? '衔接正常' : '有待修补'}
              </span>
            )}
          </div>
          {longTrip?.first_round && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-600">
              按 {longTrip.first_round.chunk_days ?? 7} 天一段逐段生成：
              {(longTrip.first_round.generated || []).length} 段已排好
              {(longTrip.first_round.failed || []).length > 0
                ? `；${(longTrip.first_round.failed || []).length} 段没生成出来（第 ${(longTrip.first_round.failed || []).join('、')} 段）`
                : ''}
              {(longTrip.first_round.skipped || []).length > 0
                ? `；${(longTrip.first_round.skipped || []).length} 段超出本次上限、还没排（第 ${(longTrip.first_round.skipped || []).join('、')} 段）`
                : ''}
              {(longTrip.first_round.out_of_range_days || []).length > 0
                ? `；模型多给了第 ${(longTrip.first_round.out_of_range_days || []).join('、')} 天，已丢弃`
                : ''}
            </p>
          )}
          <div className="mt-1.5 space-y-0.5">
            {(longTrip?.segments || []).map((item) => (
              <div key={item.index} className="flex items-baseline justify-between gap-3 text-[12.5px] text-slate-600">
                <span>
                  第 {item.index} 段 · 第 {item.start_day}-{item.end_day} 天
                </span>
                <span className="font-mono tabular-nums">
                  {item.nodes ?? 0} 个节点 · 休整 {item.rest_days ?? 0} 天
                  {item.needs_repair ? <span className="ml-1 font-bold text-amber-600">待修补</span> : null}
                </span>
              </div>
            ))}
          </div>
          {(longTrip?.repaired?.length || longTrip?.repair_failed_segments?.length) ? (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-600">
              {longTrip?.repaired?.length
                ? `已重新生成 ${longTrip.repaired.length} 段（第 ${longTrip.repaired.map((item) => item.days).join('、')} 天）`
                : ''}
              {longTrip?.repair_failed_segments?.length
                ? `${longTrip?.repaired?.length ? '；' : ''}未补上 ${longTrip.repair_failed_segments.length} 段（从第 ${longTrip.repair_failed_segments.join('、')} 天起）`
                : ''}
            </p>
          ) : null}
          {longTrip?.error && (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">
              这次没能做分段核对（{longTrip.error}），长途日程的长短是否合适未核实。
            </p>
          )}
        </div>
      )}

      {hasConstraintBlock && (
        <div className={(hasBudgetBlock || hasFallbackBlock || hasHorizonBlock) ? 'mt-4 border-t border-slate-200 pt-3' : ''}>
          <div className="flex items-center justify-between">
            <SectionLabel icon={<Target className="h-3 w-3" />}>诉求核对</SectionLabel>
            {typeof constraints?.total === 'number' && (
              <span className="font-mono text-[12.5px] font-bold tabular-nums text-slate-600">
                {constraints.total} 条
              </span>
            )}
          </div>
          {constraints?.error ? (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">
              这次没能逐条核对（{constraints.error}），你提的要求请以方案本身为准。
            </p>
          ) : (
            <>
              <ul className="mt-1.5 space-y-1">
                {(constraints?.items || []).slice(0, 8).map((item) => {
                  const tone = CONSTRAINT_STATUS[item.status || ''] || CONSTRAINT_STATUS.unverified;
                  return (
                    <li key={item.id} className="flex items-baseline gap-2 text-[12.5px] leading-relaxed">
                      <span className={`shrink-0 font-bold ${tone.tone}`}>{tone.label}</span>
                      <span className="text-slate-700">{item.text}</span>
                      {item.detail && <span className="text-slate-500">{item.detail}</span>}
                    </li>
                  );
                })}
              </ul>
              {(constraints?.items?.length ?? 0) > 8 && (
                <p className="mt-1.5 text-[12.5px] text-slate-500">
                  还有 {(constraints?.items?.length ?? 0) - 8} 条没列出来
                </p>
              )}
            </>
          )}
        </div>
      )}

      {hasReviewBlock && (
        <div className={(hasBudgetBlock || hasFallbackBlock || hasHorizonBlock || hasIncrementBlock || hasPriceBlock || hasTransportBlock || hasLongTripBlock || hasConstraintBlock) ? 'mt-4 border-t border-slate-200 pt-3' : ''}>
          <div className="flex items-center justify-between">
            <SectionLabel icon={<SlidersHorizontal className="h-3 w-3" />}>自动复核</SectionLabel>
            {typeof review?.remaining_hard === 'number' && (
              <span
                className={`font-mono text-[12.5px] font-bold tabular-nums ${
                  review.remaining_hard === 0 ? 'text-emerald-600' : 'text-amber-600'
                }`}
              >
                {review.action_count ?? 0} 处已修好
                {review.remaining_hard === 0
                  ? ' · 没有遗留问题'
                  : ` · 还剩 ${review.remaining_hard} 处要你确认`}
              </span>
            )}
          </div>
          {review?.error ? (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">
              这次没能跑自动复核（{review.error}），方案按原样给出。
            </p>
          ) : (
            <>
              {(review?.action_count ?? 0) > 0 && (
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-600">
                  已自动修正：{summarizeReviewActions(review?.actions || [])}
                </p>
              )}
              {(review?.actions || [])
                .filter((item) => item.code === 'reschedule' && item.node)
                .slice(0, 3)
                .map((item, index) => (
                  <div key={`review-${index}`} className="flex items-baseline justify-between gap-3 py-0.5 text-[12.5px] text-slate-500">
                    <span className="truncate">
                      第 {item.day} 天「{item.node}」
                    </span>
                    <span className="font-mono tabular-nums">
                      {item.from} → {item.to}
                    </span>
                  </div>
                ))}
              {(review?.needs_data || []).length > 0 && (
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-amber-600">
                  这些我改不动，要靠数据或你确认：
                  {(review?.needs_data || []).map((item) => REVIEW_NEEDS_DATA_LABELS[item] || item).join('、')}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {hasFailureBlock && (
        <div
          className={
            hasBudgetBlock || hasFallbackBlock || hasHorizonBlock || hasIncrementBlock ||
            hasPriceBlock || hasTransportBlock || hasLongTripBlock || hasReviewBlock || hasConstraintBlock
              ? 'mt-4 border-t border-slate-200 pt-3'
              : ''
          }
        >
          <SectionLabel icon={<ReceiptText className="h-3 w-3" />}>还需要你留意</SectionLabel>
          <ul className="mt-1.5 space-y-1">
            {failures.slice(0, 3).map((item, index) => (
              <li key={`${item.code}-${index}`} className="flex items-baseline gap-2 text-[12.5px] leading-relaxed">
                <span className="text-slate-500">·</span>
                <span className="font-bold text-slate-700">{GATE_LABELS[item.code || ''] || '安排需要调整'}</span>
                <span className="text-slate-500">{item.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {hasDegradationBlock && (
        <div
          className={
            hasBudgetBlock || hasFallbackBlock || hasHorizonBlock || hasIncrementBlock || hasPriceBlock ||
            hasTransportBlock || hasLongTripBlock || hasReviewBlock || hasConstraintBlock || hasFailureBlock
              ? 'mt-4 border-t border-slate-200 pt-3'
              : ''
          }
        >
          <div className="flex items-center justify-between">
            <SectionLabel icon={<AlertTriangle className="h-3 w-3" />}>数据降级</SectionLabel>
            {typeof degradations?.total === 'number' && (
              <span className="font-mono text-[12.5px] font-bold tabular-nums text-amber-600">
                {degradations.total} 项
              </span>
            )}
          </div>
          {degradations?.error ? (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">
              这次没能统计数据降级（{degradations.error}）。
            </p>
          ) : (
            <>
              <ul className="mt-1.5 space-y-1">
                {(degradations?.items || []).slice(0, 5).map((item, index) => (
                  <li key={`${item.source}-${index}`} className="text-[12.5px] leading-relaxed">
                    <span className={`font-bold ${item.status === 'missing' ? 'text-rose-600' : 'text-amber-600'}`}>
                      {item.status === 'missing' ? '这项没取到' : '只取到一部分'}
                    </span>
                    <span className="ml-1 font-bold text-slate-700">{item.label}</span>
                    <span className="ml-1 text-slate-500">{item.reason}</span>
                    {item.impact && <span className="ml-1 text-slate-500">（{item.impact}）</span>}
                  </li>
                ))}
              </ul>
              {(degradations?.items?.length ?? 0) > 5 && (
                <p className="mt-1.5 text-[12.5px] text-slate-500">
                  还有 {(degradations?.items?.length ?? 0) - 5} 项没列出来
                </p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
