'use client';

import { MapPin, PiggyBank, Clock3, ShieldAlert, Edit3 } from 'lucide-react';

// 预算可信度：来自后端 budget_report 的「已确认/待确认/未取价」三档口径。
// 只有后端真的给了金额才渲染这一块，缺字段不画空壳。
export type BudgetBreakdown = {
  confidence: string | null;
  verifiedCost: number | null;
  estimatedCost: number | null;
  unknownCount: number;
  status: string | null;
  shortfall: number | null;
};

// 后端 budget_report.confidence 只有 high/medium/low 三档，这里直译成人话；
// 读不懂的值不猜，显示「未标注」，避免把没有分级的数据说成可信。
const CONFIDENCE: Record<string, { label: string; tone: string; hint: string }> = {
  high: { label: '可信度：高', tone: 'text-emerald-700 bg-emerald-50', hint: '价格均来自供应商或可核来源' },
  medium: { label: '可信度：中', tone: 'text-amber-700 bg-amber-50', hint: '部分价格为估算，或有节点未取到价格' },
  low: { label: '可信度：低', tone: 'text-rose-700 bg-rose-50', hint: '多数节点没取到价格，总额仅供参考' },
};

const money = (value: number) => `¥${Math.round(value).toLocaleString()}`;

// 今日概览：普通模式首屏只回答四个问题——今天去哪、总共多少钱、要花多少时间在路上、
// 有没有需要处理的风险。其余指标（覆盖率、共识指数、策略臂、采样分布）收进「查看依据」。
// 所有数字都来自真实数据；拿不到就如实写「暂无」，不补零、不估算。
export default function TripOverviewCard({
  dayLabel,
  dayRoutes,
  totalDays,
  budgetTotal,
  budgetEstimated,
  unpricedCount,
  transportMinutes,
  riskHeadline,
  riskDisclosure,
  isFallbackRoute,
  budgetBreakdown,
  memberCount,
  onEdit,
}: {
  dayLabel: string;
  dayRoutes: any[];
  totalDays: number;
  budgetTotal: number | null;
  budgetEstimated: number;
  unpricedCount: number;
  transportMinutes: number | null;
  riskHeadline: string;
  riskDisclosure: string;
  isFallbackRoute: boolean;
  budgetBreakdown?: BudgetBreakdown | null;
  memberCount?: number;
  onEdit: () => void;
}) {
  const stops = dayRoutes.map((node: any) => String(node?.location || '').trim()).filter(Boolean);
  const routeText = stops.length === 0
    ? '尚未生成今日路线'
    : stops.length === 1
      ? stops[0]
      : `${stops[0]} → ${stops[stops.length - 1]}`;

  const hasVerifiedBudget = Number.isFinite(budgetTotal) && (budgetTotal as number) > 0;
  const budgetText = hasVerifiedBudget
    ? `¥${Math.round(budgetTotal as number).toLocaleString()}`
    : budgetEstimated > 0
      ? `估算 ¥${Math.round(budgetEstimated).toLocaleString()}`
      : '未给出预算';
  const budgetHint = hasVerifiedBudget
    ? '后端预算报告总额'
    : budgetEstimated > 0
      ? `按节点报价合计${unpricedCount > 0 ? `，${unpricedCount} 个节点未计价` : ''}`
      : '供应商未返回价格';

  const transportText = transportMinutes != null && transportMinutes > 0
    ? `${Math.floor(transportMinutes / 60) > 0 ? `${Math.floor(transportMinutes / 60)} 小时 ` : ''}${transportMinutes % 60} 分钟`
    : '暂无交通耗时数据';

  // 预算三档口径：已确认（供应商/可核价）+ 待确认（估算）+ 未取价节点。
  // 人均/日均按「已确认+待确认」合计分摊，分摊人数与天数都来自真实数据。
  const verified = budgetBreakdown?.verifiedCost ?? 0;
  const pending = budgetBreakdown?.estimatedCost ?? 0;
  const reportTotal = verified + pending;
  const confidenceInfo = budgetBreakdown
    ? CONFIDENCE[budgetBreakdown.confidence || ''] || {
        label: '可信度：未标注',
        tone: 'text-slate-600 bg-slate-100',
        hint: '后端未给出可信度分级',
      }
    : null;
  const people = Math.max(1, Math.round(Number(memberCount) || 1));
  const days = Math.max(1, Math.round(Number(totalDays) || 1));
  const perPersonText = reportTotal > 0 ? money(reportTotal / people) : '暂无';
  const perDayText = reportTotal > 0 ? money(reportTotal / days) : '暂无';
  const unknownText = budgetBreakdown && budgetBreakdown.unknownCount > 0
    ? `${budgetBreakdown.unknownCount} 个节点未取价`
    : '估算价，尚未核实';
  const overrunText = budgetBreakdown?.shortfall && budgetBreakdown.shortfall > 0
    ? budgetBreakdown.status === 'over'
      ? `已超出预算 ${money(budgetBreakdown.shortfall)}`
      : budgetBreakdown.status === 'at_risk'
        ? `可能超出预算 ${money(budgetBreakdown.shortfall)}（按已确认+待确认合计）`
        : null
    : null;

  return (
    <section
      className="mt-3 rounded-2xl border border-stone-200 bg-[#fbfaf7] p-3 shadow-[0_8px_30px_rgba(67,45,25,0.06)]"
      aria-label="今日概览"
      data-testid="trip-overview"
    >
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl bg-white p-3">
          <p className="flex items-center gap-1 text-[10px] font-bold text-stone-400"><MapPin className="h-3 w-3" />今日路线</p>
          <p className="mt-1 truncate text-sm font-black text-stone-800" title={routeText}>{routeText}</p>
          <p className="mt-0.5 text-[10px] text-stone-400">{dayLabel} · {dayRoutes.length} 个节点 · 共 {totalDays} 天</p>
        </div>
        <div className="rounded-xl bg-white p-3">
          <p className="flex items-center gap-1 text-[10px] font-bold text-stone-400"><PiggyBank className="h-3 w-3" />总预算</p>
          <p className={`mt-1 text-sm font-black ${hasVerifiedBudget ? 'text-stone-800' : 'text-amber-700'}`} data-testid="budget-total">{budgetText}</p>
          <p className="mt-0.5 text-[10px] text-stone-400">{budgetHint}</p>
        </div>
        <div className="rounded-xl bg-white p-3">
          <p className="flex items-center gap-1 text-[10px] font-bold text-stone-400"><Clock3 className="h-3 w-3" />交通耗时</p>
          <p className={`mt-1 text-sm font-black ${transportMinutes != null && transportMinutes > 0 ? 'text-stone-800' : 'text-amber-700'}`}>{transportText}</p>
          <p className="mt-0.5 text-[10px] text-stone-400">按已返回的分段交通耗时合计</p>
        </div>
        <div className="rounded-xl bg-white p-3">
          <p className="flex items-center gap-1 text-[10px] font-bold text-stone-400"><ShieldAlert className="h-3 w-3" />风险提醒</p>
          <p className="mt-1 line-clamp-2 text-sm font-black text-stone-800" title={riskHeadline}>{riskHeadline}</p>
          <p className="mt-0.5 line-clamp-2 text-[10px] text-stone-400">{riskDisclosure}</p>
        </div>
      </div>
      {budgetBreakdown && confidenceInfo && (
        <div
          className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4"
          data-testid="budget-credibility"
          aria-label="预算可信度"
        >
          <div className="rounded-xl bg-white p-3">
            <p className="text-[10px] font-bold text-stone-400">可信度</p>
            <p className={`mt-1 inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-black ${confidenceInfo.tone}`} data-testid="budget-confidence">
              {confidenceInfo.label}
            </p>
            <p className="mt-1 text-[10px] text-stone-400">{confidenceInfo.hint}</p>
          </div>
          <div className="rounded-xl bg-white p-3">
            <p className="text-[10px] font-bold text-stone-400">已确认</p>
            <p className="mt-1 text-sm font-black text-emerald-700" data-testid="budget-verified">
              {verified > 0 ? money(verified) : '暂无'}
            </p>
            <p className="mt-0.5 text-[10px] text-stone-400">供应商/可核来源价格</p>
          </div>
          <div className="rounded-xl bg-white p-3">
            <p className="text-[10px] font-bold text-stone-400">待确认</p>
            <p className={`mt-1 text-sm font-black ${pending > 0 ? 'text-amber-700' : 'text-stone-500'}`} data-testid="budget-pending">
              {pending > 0 ? money(pending) : '暂无'}
            </p>
            <p className="mt-0.5 text-[10px] text-stone-400">{unknownText}</p>
          </div>
          <div className="rounded-xl bg-white p-3">
            <p className="text-[10px] font-bold text-stone-400">人均 / 日均</p>
            <p className="mt-1 text-sm font-black text-stone-800" data-testid="budget-per-person">
              {perPersonText}
            </p>
            <p className="mt-0.5 text-[10px] text-stone-400">
              日均 {perDayText} · 按 {people} 人、{days} 天分摊
            </p>
          </div>
        </div>
      )}
      {overrunText && (
        <p data-testid="budget-overrun" className="mt-2 rounded-lg bg-rose-50 px-2.5 py-1.5 text-[11px] font-bold text-rose-700">
          {overrunText}
        </p>
      )}
      {isFallbackRoute && (
        <p data-testid="overview-fallback-route-badge" className="mt-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11px] font-bold text-amber-700">
          保底路线（系统兜底）：大模型暂不可用，当前方案由候选池合成。
        </p>
      )}
      <div className="mt-2 flex justify-end">
        <button
          type="button"
          data-testid="overview-edit-trip"
          onClick={onEdit}
          className="inline-flex min-h-8 items-center gap-1 rounded-lg border border-stone-300 bg-white px-2.5 text-[11px] font-bold text-stone-700 transition-colors hover:bg-stone-50"
        >
          <Edit3 className="h-3.5 w-3.5" /> 修改行程
        </button>
      </div>
    </section>
  );
}
