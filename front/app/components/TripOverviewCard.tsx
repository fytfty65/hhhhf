'use client';

import { MapPin, PiggyBank, Clock3, ShieldAlert, Edit3 } from 'lucide-react';

// 预算把握：来自后端 budget_report 的「已核价/待估算/未取价」三档口径。
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
  high: { label: '预算把握：高', tone: 'text-emerald-700 bg-emerald-50', hint: '价格均来自官方或可核来源' },
  medium: { label: '预算把握：中', tone: 'text-amber-700 bg-amber-50', hint: '部分价格为估算，个别节点待估算' },
  low: { label: '预算把握：低', tone: 'text-rose-700 bg-rose-50', hint: '多数节点还没定价，总额仅供参考' },
};

const money = (value: number) => `¥${Math.round(value).toLocaleString()}`;

// 今日概览：普通模式首屏只回答四个问题——今天去哪、总共多少钱、要花多少时间在路上、
// 有没有需要处理的风险。其余指标（覆盖率、共识指数、策略臂、采样分布）收进「查看依据」。
// 所有数字都来自真实数据；拿不到不补零、不编造，但也不把「未估计」当主文案：
// 能用已知报价给下限就写「¥x 起」，完全没有才写「待报价 / 待返回」，且用中性色而非警示橙。
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

  // 预算三档口径：已核价（供应商/可核价）+ 待估算（估算）+ 未取价节点。
  const verified = budgetBreakdown?.verifiedCost ?? 0;
  const pending = budgetBreakdown?.estimatedCost ?? 0;
  const reportTotal = verified + pending;
  const hasBudgetTotal = Number.isFinite(budgetTotal) && (budgetTotal as number) > 0;
  const budgetText = hasBudgetTotal
    ? `¥${Math.round(budgetTotal as number).toLocaleString()}`
    : budgetEstimated > 0
      ? `约 ¥${Math.round(budgetEstimated).toLocaleString()}`
      : verified > 0
        ? `${money(verified)} 起`
        : '待报价';
  // 缺失只说明「还没到」，不把「未估计」当主语；有下限时给「¥x 起」。
  const budgetHint = hasBudgetTotal
    ? reportTotal > 0
      ? pending > 0 ? '已核价 + 待估算合计' : '已核价报价合计'
      : '按已取到的节点报价合计'
    : budgetEstimated > 0
      ? `按已取到的节点报价合计${unpricedCount > 0 ? `，另有 ${unpricedCount} 个节点待估算` : ''}`
      : verified > 0
        ? '仅含已核价部分，其余待估算'
        : '报价返回后自动更新';

  const transportText = transportMinutes != null && transportMinutes > 0
    ? `${Math.floor(transportMinutes / 60) > 0 ? `${Math.floor(transportMinutes / 60)} 小时 ` : ''}${transportMinutes % 60} 分钟`
    : '待返回';

  // 人均/日均按「已核价+待估算」合计分摊，分摊人数与天数都来自真实数据。
  const confidenceInfo = budgetBreakdown
    ? CONFIDENCE[budgetBreakdown.confidence || ''] || {
        label: '预算把握：未标注',
        tone: 'text-slate-600 bg-slate-100',
        hint: '这份预算未标注分级',
      }
    : null;
  const people = Math.max(1, Math.round(Number(memberCount) || 1));
  const days = Math.max(1, Math.round(Number(totalDays) || 1));
  const perPersonText = reportTotal > 0 ? money(reportTotal / people) : '待估算';
  const perDayText = reportTotal > 0 ? money(reportTotal / days) : '待估算';
  const unknownText = budgetBreakdown && budgetBreakdown.unknownCount > 0
    ? `${budgetBreakdown.unknownCount} 个节点待估算`
    : '按已取到的报价估算';
  // 一个金额都没取到、也没有待估算节点时整块不渲染，避免只剩负面卡。
  const hasBudgetDetail = !!budgetBreakdown
    && (verified > 0 || pending > 0 || (budgetBreakdown.unknownCount ?? 0) > 0);
  const overrunText = budgetBreakdown?.shortfall && budgetBreakdown.shortfall > 0
    ? budgetBreakdown.status === 'over'
      ? `已超出预算 ${money(budgetBreakdown.shortfall)}`
      : budgetBreakdown.status === 'at_risk'
        ? `可能超出预算 ${money(budgetBreakdown.shortfall)}（按已核价+待估算合计）`
        : null
    : null;

  // 首屏一行结论：节点数 · 在途时间 · 人均预算，全部来自已有 props。
  const summaryTransport = transportMinutes != null && transportMinutes > 0
    ? transportMinutes >= 60
      ? `约 ${Math.round((transportMinutes / 60) * 10) / 10} 小时在途`
      : `约 ${transportMinutes} 分钟在途`
    : '在途时间待估算';
  const summaryPerPerson = reportTotal > 0
    ? `预计 ${money(reportTotal / people)}/人`
    : hasBudgetTotal
      ? `预计 ${money((budgetTotal as number) / people)}/人`
      : budgetEstimated > 0
        ? `预计 ${money(budgetEstimated / people)}/人`
        : '人均待估算';
  const overviewSummary = `${dayRoutes.length} 个节点 · ${summaryTransport} · ${summaryPerPerson}`;

  return (
    <section
      className="mt-3 rounded-2xl border border-stone-200 bg-[#fbfaf7] p-3 shadow-[0_8px_30px_rgba(67,45,25,0.06)]"
      aria-label="今日概览"
      data-testid="trip-overview"
    >
      <p className="mb-2 truncate text-xs font-medium text-stone-600" title={overviewSummary}>
        {overviewSummary}
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div className="rounded-xl bg-white p-3">
          <p className="flex items-center gap-1 text-xs font-bold text-stone-400"><MapPin className="h-3 w-3" />今日路线</p>
          <p className="mt-1 truncate text-sm font-black text-stone-800" title={routeText}>{routeText}</p>
          <p className="mt-0.5 text-xs text-stone-400">{dayLabel} · {dayRoutes.length} 个节点 · 共 {totalDays} 天</p>
        </div>
        <div className="rounded-xl bg-white p-3">
          <p className="flex items-center gap-1 text-xs font-bold text-stone-400"><PiggyBank className="h-3 w-3" />总预算</p>
          <p className={`mt-1 text-sm font-black ${hasBudgetTotal ? 'text-stone-800' : budgetEstimated > 0 ? 'text-amber-700' : 'text-stone-500'}`} data-testid="budget-total">{budgetText}</p>
          <p className="mt-0.5 text-xs text-stone-400">{budgetHint}</p>
        </div>
        <div className="rounded-xl bg-white p-3">
          <p className="flex items-center gap-1 text-xs font-bold text-stone-400"><Clock3 className="h-3 w-3" />交通耗时</p>
          <p className={`mt-1 text-sm font-black ${transportMinutes != null && transportMinutes > 0 ? 'text-stone-800' : 'text-stone-500'}`}>{transportText}</p>
          <p className="mt-0.5 text-xs text-stone-400">按分段交通耗时汇总</p>
        </div>
        <div className="rounded-xl bg-white p-3">
          <p className="flex items-center gap-1 text-xs font-bold text-stone-400"><ShieldAlert className="h-3 w-3" />风险提醒</p>
          <p className="mt-1 line-clamp-2 text-sm font-black text-stone-800" title={riskHeadline}>{riskHeadline}</p>
          <p className="mt-0.5 line-clamp-2 text-xs text-stone-400">{riskDisclosure}</p>
        </div>
      </div>
      {budgetBreakdown && confidenceInfo && hasBudgetDetail && (
        <div
          className="mt-2 flex flex-wrap gap-2"
          data-testid="budget-credibility"
          aria-label="预算把握"
        >
          <div className="min-w-[150px] flex-1 rounded-xl bg-white p-3">
            <p className="text-xs font-bold text-stone-400">预算把握</p>
            <p className={`mt-1 inline-flex rounded-md px-1.5 py-0.5 text-xs font-black ${confidenceInfo.tone}`} data-testid="budget-confidence">
              {confidenceInfo.label}
            </p>
            <p className="mt-1 text-xs text-stone-400">{confidenceInfo.hint}</p>
          </div>
          {verified > 0 && (
            <div className="min-w-[132px] flex-1 rounded-xl bg-white p-3">
              <p className="text-xs font-bold text-stone-400">已核价</p>
              <p className="mt-1 text-sm font-black text-emerald-700" data-testid="budget-verified">
                {money(verified)}
              </p>
              <p className="mt-0.5 text-xs text-stone-400">官方/可核来源价格</p>
            </div>
          )}
          {pending > 0 && (
            <div className="min-w-[132px] flex-1 rounded-xl bg-white p-3">
              <p className="text-xs font-bold text-stone-400">待估算</p>
              <p className="mt-1 text-sm font-black text-amber-700" data-testid="budget-pending">
                {money(pending)}
              </p>
              <p className="mt-0.5 text-xs text-stone-400">{unknownText}</p>
            </div>
          )}
          <div className="min-w-[150px] flex-1 rounded-xl bg-white p-3">
            <p className="text-xs font-bold text-stone-400">人均 / 日均</p>
            <p className={`mt-1 text-sm font-black ${reportTotal > 0 ? 'text-stone-800' : 'text-stone-500'}`} data-testid="budget-per-person">
              {perPersonText}
            </p>
            <p className="mt-0.5 text-xs text-stone-400">
              日均 {perDayText} · 按 {people} 人、{days} 天分摊
            </p>
          </div>
        </div>
      )}
      {overrunText && (
        <p data-testid="budget-overrun" className="mt-2 rounded-lg bg-rose-50 px-2.5 py-1.5 text-xs font-bold text-rose-700">
          {overrunText}
        </p>
      )}
      {isFallbackRoute && (
        <p data-testid="overview-fallback-route-badge" className="mt-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs font-bold text-amber-700">
          保底路线（系统兜底）：大模型暂不可用，当前方案由候选池合成。
        </p>
      )}
      <div className="mt-2 flex justify-end">
        <button
          type="button"
          data-testid="overview-edit-trip"
          onClick={onEdit}
          className="inline-flex min-h-8 items-center gap-1 rounded-lg border border-stone-300 bg-white px-2.5 text-xs font-bold text-stone-700 transition-colors hover:bg-stone-50"
        >
          <Edit3 className="h-3.5 w-3.5" /> 修改行程
        </button>
      </div>
    </section>
  );
}
