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
import { ReceiptText, SlidersHorizontal, CalendarRange } from 'lucide-react';

type GateFailure = { code?: string; detail?: string };
type Substitution = { from?: string; to?: string; saving?: number | null; reason?: string; kind?: string };
type Segment = { start?: number; end?: number; days?: number };

export type PlanGovernance = {
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
};

const BUDGET_STATUS: Record<string, { label: string; tone: string }> = {
  over: { label: '超出预算', tone: 'text-rose-600' },
  at_risk: { label: '可能超支', tone: 'text-amber-600' },
  unverifiable: { label: '价格未核实，暂无法判定', tone: 'text-amber-600' },
  no_budget: { label: '未设置预算', tone: 'text-slate-500' },
  ok: { label: '在预算内', tone: 'text-emerald-600' },
};

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
    <div className="flex items-center gap-1.5 text-[10px] font-bold tracking-wide text-slate-400">
      {icon}
      <span>{children}</span>
    </div>
  );
}

function Row({ label, value, tone, hint }: { label: string; value: React.ReactNode; tone?: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-[11px] text-slate-500" title={hint}>{label}</span>
      <span className={`font-mono text-[12px] font-bold tabular-nums ${tone || 'text-slate-800'}`}>{value}</span>
    </div>
  );
}

export default function PlanGovernancePanel({ data }: { data?: PlanGovernance | null }) {
  if (!data) return null;
  const { quality, budget, fallback, horizon } = data;

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
  if (!hasBudgetBlock && !hasFallbackBlock && !hasHorizonBlock && !hasFailureBlock) return null;

  return (
    <section
      className="mt-3 rounded-xl border border-slate-200 bg-slate-50/70 px-3.5 py-3 text-left"
      aria-label="行程核对说明"
    >
      {hasBudgetBlock && (
        <div>
          <div className="flex items-center justify-between">
            <SectionLabel icon={<ReceiptText className="h-3 w-3" />}>预算核对</SectionLabel>
            <span className={`text-[11px] font-bold ${budgetStatus?.tone}`}>{budgetStatus?.label}</span>
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
        <div className={hasBudgetBlock ? 'mt-3 border-t border-slate-200 pt-2.5' : ''}>
          <SectionLabel icon={<SlidersHorizontal className="h-3 w-3" />}>已作调整</SectionLabel>
          {fallback?.disclosure && (
            <p className="mt-1.5 text-[11px] leading-relaxed text-slate-600">{fallback.disclosure}</p>
          )}
          {substitutions.length > 0 && (
            <ul className="mt-1.5 space-y-1">
              {substitutions.slice(0, 5).map((item, index) => (
                <li key={`${item.from || item.kind}-${index}`} className="flex items-baseline gap-2 text-[11px] text-slate-600">
                  <span className="text-slate-400">·</span>
                  {item.from && item.to ? (
                    <span>
                      把「{item.from}」换成「{item.to}」
                      {typeof item.saving === 'number' && item.saving > 0 && (
                        <span className="ml-1 font-mono font-bold tabular-nums text-emerald-600">省 {money(item.saving)}</span>
                      )}
                    </span>
                  ) : (
                    <span>{item.reason}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {fallback?.needs_confirmation && (
            <p className="mt-1.5 text-[11px] font-bold text-amber-600">
              还有缺口，只能通过改天数或换城市才能补上——这类改动我不会自动做，你确认了我再调整。
            </p>
          )}
        </div>
      )}

      {hasHorizonBlock && (
        <div className={(hasBudgetBlock || hasFallbackBlock) ? 'mt-3 border-t border-slate-200 pt-2.5' : ''}>
          <div className="flex items-center justify-between">
            <SectionLabel icon={<CalendarRange className="h-3 w-3" />}>行程规模</SectionLabel>
            <span className="font-mono text-[11px] font-bold tabular-nums text-slate-600">
              {horizon?.days} 天
              {segments.length > 1 && ` · ${segments.length} 段（${segments.map((s) => s.days).join('/')}）`}
            </span>
          </div>
          <ul className="mt-1.5 space-y-1">
            {advisories.slice(0, 3).map((text, index) => (
              <li key={index} className="flex items-baseline gap-2 text-[11px] leading-relaxed text-slate-600">
                <span className="text-slate-400">·</span>
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {hasFailureBlock && (
        <div className={(hasBudgetBlock || hasFallbackBlock || hasHorizonBlock) ? 'mt-3 border-t border-slate-200 pt-2.5' : ''}>
          <SectionLabel icon={<ReceiptText className="h-3 w-3" />}>还需要你留意</SectionLabel>
          <ul className="mt-1.5 space-y-1">
            {failures.slice(0, 3).map((item, index) => (
              <li key={`${item.code}-${index}`} className="flex items-baseline gap-2 text-[11px] leading-relaxed">
                <span className="text-slate-400">·</span>
                <span className="font-bold text-slate-700">{GATE_LABELS[item.code || ''] || '安排需要调整'}</span>
                <span className="text-slate-500">{item.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
