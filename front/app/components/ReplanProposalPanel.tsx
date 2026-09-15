'use client';

import React from 'react';
import { Check, ShieldCheck, X } from 'lucide-react';

type Props = {
  proposal: any;
  busy?: boolean;
  onApply: (candidate: any) => void;
  onReject: () => void;
};

export default function ReplanProposalPanel({ proposal, busy = false, onApply, onReject }: Props) {
  if (!proposal) return null;
  const allCandidates = Array.isArray(proposal.data?.replacement_candidates) ? proposal.data.replacement_candidates : [];
  const paretoFrontier = Array.isArray(proposal.data?.pareto?.frontier) ? proposal.data.pareto.frontier : [];
  const candidates = paretoFrontier.length > 0 ? paretoFrontier : allCandidates;
  const rejected = Array.isArray(proposal.data?.pareto?.rejected) ? proposal.data.pareto.rejected : [];
  const fairness = proposal.data?.fairness;
  const fairnessBlocked = fairness?.status === 'evaluated' && fairness.threshold_exceeded === true;
  const action = proposal.action || {};
  const affected = Array.isArray(action.affected_nodes) ? action.affected_nodes : [];
  const preserved = Array.isArray(action.preserve_nodes) ? action.preserve_nodes : [];
  const multiNodeBlocked = affected.length !== 1;

  return (
    <section className="mx-4 mt-4 overflow-hidden rounded-xl border border-orange-200 bg-orange-50/80 shadow-sm sm:mx-8" aria-label="应急重规划提案">
      <div className="flex items-start justify-between gap-3 border-b border-orange-100 px-4 py-3">
        <div className="flex min-w-0 items-start gap-2">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-orange-600" />
          <div className="min-w-0">
            <h3 className="text-sm font-black text-slate-800">{action.title || '应急重规划提案'}</h3>
            <p className="mt-1 text-[11px] leading-5 text-slate-600">{action.reason || '仅修改受影响节点，其他安排保持不变。'}</p>
          </div>
        </div>
        <button type="button" onClick={onReject} disabled={busy} className="rounded-md p-1 text-slate-400 transition hover:bg-white hover:text-slate-700 disabled:opacity-50" title="拒绝提案" aria-label="拒绝提案"><X className="h-4 w-4" /></button>
      </div>
      <div className="grid gap-2 px-4 py-3 text-[11px] sm:grid-cols-2">
        <div><span className="font-bold text-rose-700">待调整 {affected.length}</span><span className="ml-2 text-slate-500">{affected.join('、') || '待确认节点'}</span></div>
        <div><span className="font-bold text-emerald-700">保留 {preserved.length}</span><span className="ml-2 text-slate-500">未受影响安排</span></div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-orange-100 px-4 py-2 text-[10px] text-slate-500">
        <span>Pareto 保留 {candidates.length} / {allCandidates.length}</span>
        {fairness?.status === 'evaluated' ? <span className={fairness.threshold_exceeded ? 'font-bold text-rose-600' : 'text-emerald-700'}>成员公平标准差 {Number(fairness.utility_stddev || 0).toFixed(3)} / 上限 {Number(fairness.threshold || 0).toFixed(3)}</span> : <span>多人满意度：待成员效用数据确认</span>}
      </div>
      {fairnessBlocked && <div className="border-t border-rose-100 bg-rose-50 px-4 py-2 text-[11px] font-bold text-rose-700">成员公平指标超过阈值，当前提案不能直接应用，请重新选择方案或调整成员偏好。</div>}
      {multiNodeBlocked && <div className="border-t border-amber-100 bg-amber-50 px-4 py-2 text-[11px] font-bold text-amber-700">当前提案包含多个待调整节点，请拆分为逐节点提案后再应用，避免一个候选覆盖多个地点。</div>}
      {candidates.length > 0 ? (
        <div className="space-y-2 border-t border-orange-100 px-4 py-3">
          <p className="text-[10px] font-black uppercase tracking-wider text-slate-500">替代候选 · 请选择一项应用</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {candidates.slice(0, 6).map((candidate: any, index: number) => (
              <button key={`${candidate.id || candidate.name || index}`} type="button" onClick={() => onApply(candidate)} disabled={busy || fairnessBlocked || multiNodeBlocked} className="flex min-h-12 items-center justify-between gap-3 rounded-lg border border-white bg-white px-3 py-2 text-left shadow-sm transition hover:border-orange-300 hover:shadow disabled:cursor-not-allowed disabled:opacity-60">
                <span className="min-w-0"><span className="block truncate text-xs font-bold text-slate-800">{candidate.name || candidate.location || '未命名候选'}</span><span className="mt-0.5 block truncate text-[10px] text-slate-400">{candidate.source?.provider || candidate.source?.Provider || '供应商候选'} · 需再次校验</span></span>
                <Check className="h-4 w-4 shrink-0 text-orange-500" />
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="border-t border-orange-100 px-4 py-3 text-[11px] text-slate-500">当前没有可验证的供应商候选，提案暂不能应用。</div>
      )}
      {rejected.length > 0 && <div className="border-t border-orange-100 px-4 py-2 text-[10px] text-slate-400">已淘汰 {rejected.length} 项：{rejected.map((item: any) => `${item.candidate}（被 ${Array.isArray(item.dominated_by) ? item.dominated_by.join('、') : '其他候选'} 支配）`).join('；')}</div>}
    </section>
  );
}
