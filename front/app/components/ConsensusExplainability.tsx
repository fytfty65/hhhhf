'use client';

import { useMemo } from 'react';
import { BrainCircuit, CircleHelp } from 'lucide-react';
import { scoreRouteForConsensus, type ConsensusMemberPreference, type ConsensusRouteNode } from '../lib/consensus';

export default function ConsensusExplainability({ route, members = [] }: { route: ConsensusRouteNode[]; members?: ConsensusMemberPreference[] }) {
  const score = useMemo(() => scoreRouteForConsensus(route, members), [route, members]);
  const dimensions = [
    ['预算', score.dimensions.budget, 'bg-emerald-500'],
    ['节奏', score.dimensions.pace, 'bg-blue-500'],
    ['风险', score.dimensions.risk, 'bg-amber-500'],
    ['兴趣', score.dimensions.interest, 'bg-orange-500'],
  ] as const;
  const memberRows = (members || []).map((member) => {
    const tags = new Set((member.interestTags || []).map((tag) => String(tag).toLowerCase()));
    const hits = (route || []).filter((node) => (node.tags || []).some((tag) => tags.has(String(tag).toLowerCase()))).length;
    const utility = tags.size ? Math.min(100, Math.round((hits / Math.max(1, route.length)) * 100)) : score.overall;
    return { ...member, utility };
  });
  const utilities = memberRows.map((member) => member.utility);
  const minUtility = utilities.length ? Math.min(...utilities) : score.overall;
  const variance = utilities.length ? utilities.reduce((sum, value) => sum + Math.pow(value - utilities.reduce((a, b) => a + b, 0) / utilities.length, 2), 0) / utilities.length : 0;
  return (
    <section className="mx-6 my-4 border border-slate-200 bg-white rounded-xl p-4 shadow-sm" aria-label="路线共识解释">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <BrainCircuit className="w-4 h-4 text-orange-500" />
          <div>
            <h3 className="text-sm font-black text-slate-800">路线共识解释</h3>
            <p className="text-[11px] text-slate-500">综合匹配度 {score.overall}% · 每项分数都可追溯</p>
          </div>
        </div>
        <CircleHelp className="w-4 h-4 text-slate-400" aria-hidden="true" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {dimensions.map(([label, dimension, color]) => (
          <div key={label} title={dimension.reason}>
            <div className="flex items-center justify-between text-[11px] font-bold text-slate-600 mb-1">
              <span>{label}</span><span>{dimension.score}</span>
            </div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div className={`h-full ${color} rounded-full`} style={{ width: `${dimension.score}%` }} />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2 border-y border-slate-100 py-3" aria-label="群体公平指标">
        <div><p className="text-[10px] text-slate-400">最低成员效用</p><p className="mt-1 text-lg font-black text-slate-800">{minUtility}<span className="text-[10px] font-normal text-slate-400"> / 100</span></p></div>
        <div><p className="text-[10px] text-slate-400">效用方差</p><p className="mt-1 text-lg font-black text-slate-800">{variance.toFixed(1)}</p></div>
        <div><p className="text-[10px] text-slate-400">裁决策略</p><p className="mt-1 text-xs font-black text-emerald-700">最大化最低满意度</p></div>
      </div>
      {memberRows.length > 0 && <div className="mt-3 space-y-1.5"><p className="text-[10px] font-bold text-slate-400">成员效用明细</p>{memberRows.map((member) => <div key={member.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px]"><span className="truncate text-slate-600">{member.name || member.id}</span><span className={member.utility >= 60 ? 'font-bold text-emerald-600' : 'font-bold text-amber-600'}>{member.utility}</span></div>)}</div>}
      <ul className="mt-3 space-y-1 text-[11px] text-slate-500">
        {score.reasons.map((reason) => <li key={reason}>· {reason}</li>)}
      </ul>
    </section>
  );
}
