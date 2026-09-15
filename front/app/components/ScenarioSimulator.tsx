'use client';

import { useMemo, useState } from 'react';
import { ArrowRight, SlidersHorizontal } from 'lucide-react';
import { scoreRouteForConsensus, type ConsensusMemberPreference, type ConsensusRouteNode } from '../lib/consensus';

export default function ScenarioSimulator({ route, members = [], onApply }: { route: ConsensusRouteNode[]; members?: ConsensusMemberPreference[]; onApply?: (constraints: { budgetLimit: number; paceLimit: number; riskTolerance: number }) => void }) {
  const [budgetLimit, setBudgetLimit] = useState(180);
  const [paceLimit, setPaceLimit] = useState(3);
  const [riskTolerance, setRiskTolerance] = useState(10);
  const score = useMemo(() => scoreRouteForConsensus(route, members, { budgetLimit, paceLimit, riskTolerance }), [route, members, budgetLimit, paceLimit, riskTolerance]);
  return (
    <section className="mx-6 my-4 border border-slate-200 bg-slate-50 rounded-xl p-4" aria-label="行程情景模拟器">
      <div className="flex items-center gap-2 mb-3">
        <SlidersHorizontal className="w-4 h-4 text-blue-600" />
        <div>
          <h3 className="text-sm font-black text-slate-800">如果这样调整，会发生什么？</h3>
          <p className="text-[11px] text-slate-500">拖动约束，实时预估当前路线的匹配度</p>
        </div>
        <strong className="ml-auto text-lg text-blue-600">{score.overall}%</strong>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor="budget-slider" className="text-[11px] font-bold text-slate-600 cursor-pointer">人均预算上限 ¥{budgetLimit}</label>
          <input id="budget-slider" className="w-full h-6 accent-emerald-500 cursor-pointer" style={{ appearance: 'auto', touchAction: 'manipulation' }} type="range" min="50" max="1000" step="10" value={budgetLimit} onChange={(event) => setBudgetLimit(Number(event.target.value))} />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="pace-slider" className="text-[11px] font-bold text-slate-600 cursor-pointer">每日最多 {paceLimit} 个节点</label>
          <input id="pace-slider" className="w-full h-6 accent-blue-500 cursor-pointer" style={{ appearance: 'auto', touchAction: 'manipulation' }} type="range" min="1" max="8" step="1" value={paceLimit} onChange={(event) => setPaceLimit(Number(event.target.value))} />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="risk-slider" className="text-[11px] font-bold text-slate-600 cursor-pointer">风险容忍度 {riskTolerance}</label>
          <input id="risk-slider" className="w-full h-6 accent-amber-500 cursor-pointer" style={{ appearance: 'auto', touchAction: 'manipulation' }} type="range" min="0" max="80" step="5" value={riskTolerance} onChange={(event) => setRiskTolerance(Number(event.target.value))} />
        </div>
      </div>
      <p className="mt-3 text-[11px] text-slate-500" aria-live="polite">
        {score.reasons[0]}；{score.reasons[1]}。{members.length ? `已纳入 ${members.length} 位同行偏好。` : '同行偏好尚未同步，当前使用中性估计。'}
      </p>
      {onApply && (
        <button
          type="button"
          onClick={() => onApply({ budgetLimit, paceLimit, riskTolerance })}
          className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-[11px] font-bold text-blue-700 transition-colors hover:bg-blue-100"
        >
          <ArrowRight className="h-3.5 w-3.5" /> 带入下一轮规划
        </button>
      )}
    </section>
  );
}
