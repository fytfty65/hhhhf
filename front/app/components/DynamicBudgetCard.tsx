'use client';

/**
 * DynamicBudgetCard — 行程预算拆解卡片（住宿/餐饮/门票/交通四宫格 + 日均）
 *
 * Extracted verbatim from ContextualLobby.tsx during the file split (批 3)。
 * 仅搬运：props 签名、类名与文案逐字保留，未做任何行为改动。
 */


export default function DynamicBudgetCard({ summary, budgetData }: { summary?: string; budgetData?: any }) {
  if (!summary && !budgetData) return null;

  const mode = budgetData?.budget_mode || 'VALUE_COST_EFFECTIVE';
  const total = budgetData?.total_budget || 0;

  return (
    <div className="mb-6 p-4 rounded-2xl bg-gradient-to-r from-emerald-50 to-teal-50 border border-emerald-200 dark:bg-slate-800 dark:border-slate-700 shadow-xs">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className={`p-1.5 rounded-lg font-bold text-xs text-white ${
            mode === 'HIGH_LUXURY' ? 'bg-purple-600' : mode === 'EXACT_AMOUNT' ? 'bg-emerald-600' : 'bg-orange-500'
          }`}>
            {mode === 'HIGH_LUXURY' ? '💎 臻选高预算' : mode === 'EXACT_AMOUNT' ? '💰 专属定额精算' : '✨ 高性价比方案'}
          </div>
          <h4 className="font-black text-sm text-slate-800 dark:text-white">
            {mode === 'EXACT_AMOUNT' ? `${total} 元行程预算分解` : mode === 'HIGH_LUXURY' ? '品质奢华花销拆解' : '性价比预估花销'}
          </h4>
        </div>
        {budgetData?.daily_avg > 0 && (
          <span className="text-[11px] font-mono text-emerald-700 dark:text-emerald-400 bg-emerald-100 dark:bg-slate-700 px-2 py-0.5 rounded-md font-bold">
            日均: ¥{budgetData.daily_avg}/天
          </span>
        )}
      </div>

      <p className="text-xs text-slate-600 dark:text-slate-300 leading-relaxed font-medium">
        {summary}
      </p>

      {budgetData && (
        <div className="grid grid-cols-4 gap-2 mt-3 text-center">
          <div className="bg-white/80 dark:bg-slate-900/60 p-2 rounded-xl border border-emerald-100 dark:border-slate-700">
            <p className="text-[10px] text-slate-400 font-bold">🏨 住宿预留</p>
            <p className="text-xs font-black text-emerald-600 dark:text-emerald-400 mt-0.5">¥{budgetData.hotel}</p>
          </div>
          <div className="bg-white/80 dark:bg-slate-900/60 p-2 rounded-xl border border-emerald-100 dark:border-slate-700">
            <p className="text-[10px] text-slate-400 font-bold">🍲 餐饮寻味</p>
            <p className="text-xs font-black text-orange-500 mt-0.5">¥{budgetData.dining}</p>
          </div>
          <div className="bg-white/80 dark:bg-slate-900/60 p-2 rounded-xl border border-emerald-100 dark:border-slate-700">
            <p className="text-[10px] text-slate-400 font-bold">🎟️ 门票体验</p>
            <p className="text-xs font-black text-indigo-500 mt-0.5">¥{budgetData.ticket}</p>
          </div>
          <div className="bg-white/80 dark:bg-slate-900/60 p-2 rounded-xl border border-emerald-100 dark:border-slate-700">
            <p className="text-[10px] text-slate-400 font-bold">🚗 交通备用</p>
            <p className="text-xs font-black text-slate-700 dark:text-slate-300 mt-0.5">¥{budgetData.traffic}</p>
          </div>
        </div>
      )}
    </div>
  );
}
