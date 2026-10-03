'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Clock3, Flag, Loader2, RefreshCw, SkipForward, TimerReset } from 'lucide-react';
import { API_BASE, apiJson } from '../lib/utils';

type Props = { tripId: string; planId?: string; routes: any[]; onRequestReplan?: (node: any) => void };
type ExecutionState = { node_key: string; status: string; delay_minutes?: number; note?: string; updated_at?: string };

const statusLabel: Record<string, string> = { planned: '待执行', in_progress: '进行中', visited: '已到访', skipped: '已跳过', delayed: '已延误' };

export default function TripExecutionPanel({ tripId, planId, routes, onRequestReplan }: Props) {
  const [states, setStates] = useState<Record<string, ExecutionState>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [delayNode, setDelayNode] = useState<string | null>(null);
  const [journey, setJourney] = useState<any>(null);
  const [pendingProposal, setPendingProposal] = useState<any>(null);

  const nodes = useMemo(() => (Array.isArray(routes) ? routes : []).filter((node) => String(node?.name || '').trim()).map((node) => ({
    ...node,
    nodeKey: `${Number(node?.day) || 1}:${String(node.name).trim()}`,
  })), [routes]);

  const load = useCallback(async () => {
    if (!tripId) return;
    try {
      const [payload, journeyPayload] = await Promise.all([
        apiJson<any>(`${API_BASE}/api/v1/planning/execution?trip_id=${encodeURIComponent(tripId)}`),
        apiJson<any>(`${API_BASE}/api/v1/planning/journey/state?trip_id=${encodeURIComponent(tripId)}`),
      ]);
      const rows = payload?.data?.states || payload?.states || [];
      setStates(Object.fromEntries((Array.isArray(rows) ? rows : []).map((row: ExecutionState) => [row.node_key, row])));
      setJourney(journeyPayload?.data || journeyPayload || null);
    } catch (e) { setError(e instanceof Error ? e.message : '执行状态同步失败'); }
  }, [tripId]);

  useEffect(() => { void load(); }, [load]);

  const update = async (node: any, status: string, delayMinutes = 0) => {
    setBusy(true); setError('');
    try {
      const payload = await apiJson<any>(`${API_BASE}/api/v1/planning/execution`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trip_id: tripId, plan_id: planId || undefined, node_key: node.nodeKey, status, delay_minutes: delayMinutes, note: node.name }),
      });
      const state = payload?.data?.state || payload?.state;
      if (state) setStates((previous) => ({ ...previous, [node.nodeKey]: state })); else await load();
      const proposal = payload?.data?.replan_proposal || payload?.replan_proposal;
      if (proposal?.status === 'pending_confirmation') setPendingProposal(proposal);
      setDelayNode(null);
    } catch (e) { setError(e instanceof Error ? e.message : '执行状态保存失败'); }
    finally { setBusy(false); }
  };

  const currentIndex = nodes.findIndex((node) => !['visited', 'skipped'].includes(states[node.nodeKey]?.status));
  const current = currentIndex >= 0 ? nodes[currentIndex] : null;
  const completedCount = nodes.filter((node) => ['visited', 'skipped'].includes(states[node.nodeKey]?.status)).length;
  const progress = nodes.length ? Math.round((completedCount / nodes.length) * 100) : 0;
  if (!tripId || nodes.length === 0) return null;

  return <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-label="旅中执行模式">
    <div className="flex items-center justify-between gap-3">
      <div>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Flag className="h-4 w-4 text-orange-700" />旅中执行</h3>
        <p className="mt-1 text-xs text-slate-500">记录实际进度；重排前会征求你的确认</p>
      </div>
      <button type="button" onClick={() => void load()} className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500" title="同步执行状态" aria-label="同步执行状态"><RefreshCw className="h-4 w-4" /></button>
    </div>
    {current && <div className="mt-4 rounded-lg border border-orange-200 bg-orange-50/60 p-4">
      <div className="flex items-start justify-between gap-3">
        <div><p className="text-[11px] font-semibold text-orange-800">接下来 · 第{Number(current.day) || 1}天</p><p className="mt-1 text-base font-semibold leading-snug text-slate-900">{current.name}</p></div>
        <Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-orange-700" />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={() => void update(current, 'in_progress')} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-slate-900 px-3 text-xs font-semibold text-white transition-colors hover:bg-slate-700 disabled:opacity-50"><TimerReset className="h-3.5 w-3.5" />开始</button>
        <button type="button" disabled={busy} onClick={() => void update(current, 'visited')} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-emerald-700 px-3 text-xs font-semibold text-white transition-colors hover:bg-emerald-800 disabled:opacity-50"><CheckCircle2 className="h-3.5 w-3.5" />标记到访</button>
        <button type="button" disabled={busy} onClick={() => void update(current, 'skipped')} className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-50"><SkipForward className="h-3.5 w-3.5" />跳过</button>
        <button type="button" onClick={() => setDelayNode(current.nodeKey)} className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50"><Clock3 className="h-3.5 w-3.5" />记录延误</button>
      </div>
      {delayNode === current.nodeKey && <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-orange-200 pt-3">
        <label htmlFor="execution-delay" className="text-xs text-slate-600">延误时长</label><input id="execution-delay" type="number" min={1} max={1440} defaultValue={30} className="h-9 w-24 rounded-md border border-slate-300 bg-white px-2 text-sm text-slate-800 focus:border-orange-500 focus:outline-none focus:ring-2 focus:ring-orange-100" /><span className="text-xs text-slate-500">分钟</span>
        <button type="button" disabled={busy} onClick={() => { const input = document.getElementById('execution-delay') as HTMLInputElement | null; void update(current, 'delayed', Math.max(1, Number(input?.value) || 30)); }} className="min-h-9 rounded-md bg-slate-900 px-3 text-xs font-semibold text-white hover:bg-slate-700 disabled:opacity-50">保存</button>
      </div>}
    </div>}
    {pendingProposal && current && <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3"><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold text-rose-800">有一项行程调整待确认</p><p className="mt-1 text-xs leading-5 text-rose-700">不会自动修改已到访节点，你可以先查看调整方案。</p></div><button type="button" onClick={() => { setPendingProposal(null); onRequestReplan?.(current); }} className="shrink-0 rounded-md bg-rose-700 px-3 py-2 text-xs font-semibold text-white hover:bg-rose-800">查看方案</button></div><p className="mt-2 text-[11px] text-rose-700">延误 {pendingProposal.delay_minutes || 0} 分钟</p></div>}
    <div className="mt-4 flex items-center justify-between text-xs text-slate-500"><span>完成 {completedCount} / {nodes.length} 个地点</span><span className="font-semibold text-slate-700">{progress}%</span></div>
    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-label="行程完成进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><div className="h-full rounded-full bg-emerald-700 transition-all" style={{ width: `${progress}%` }} /></div>
    <div className="mt-3 grid gap-2 sm:grid-cols-2">{nodes.slice(0, 8).map((node) => <div key={node.nodeKey} className="flex min-w-0 items-center justify-between gap-2 rounded-md border border-slate-200 px-3 py-2 text-xs">
      <span className="min-w-0 truncate text-slate-700"><span className="mr-1.5 font-medium text-slate-400">D{Number(node.day) || 1}</span>{node.name}</span>
      <span className={`shrink-0 text-[11px] font-medium ${states[node.nodeKey]?.status === 'visited' ? 'text-emerald-700' : states[node.nodeKey]?.status === 'skipped' ? 'text-slate-400' : states[node.nodeKey]?.status === 'delayed' ? 'text-amber-700' : states[node.nodeKey]?.status === 'in_progress' ? 'text-orange-700' : 'text-slate-500'}`}>{statusLabel[states[node.nodeKey]?.status || 'planned']}</span>
    </div>)}</div>
    <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3 text-xs">
      <span className="text-slate-500">{journey?.risk && <strong className="mr-3 text-rose-700">风险提案：{journey.risk.status === 'pending_confirmation' ? '待确认' : journey.risk.status === 'expired' ? '已过期' : journey.risk.status === 'rejected' ? '已拒绝' : journey.risk.status === 'applied' ? '已应用' : journey.risk.status}</strong>}{Array.isArray(journey?.risk?.proposal?.trigger_types) && journey.risk.proposal.trigger_types.length > 0 && <span className="mr-3">触发：{journey.risk.proposal.trigger_types.join('、')}</span>}{journey?.budget?.level === 'over' && <strong className="text-rose-700">预算已超支</strong>}</span>
      {current && onRequestReplan && <button type="button" onClick={() => onRequestReplan(current)} className="font-semibold text-orange-800 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500">仅重排剩余路线</button>}
    </div>
    {error && <p role="alert" className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-xs font-medium text-rose-800">{error}</p>}
    {busy && <p role="status" className="mt-3 flex items-center gap-1.5 text-xs text-slate-500"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在同步</p>}
  </section>;
}
