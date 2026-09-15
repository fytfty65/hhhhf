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
      setDelayNode(null);
    } catch (e) { setError(e instanceof Error ? e.message : '执行状态保存失败'); }
    finally { setBusy(false); }
  };

  const currentIndex = nodes.findIndex((node) => !['visited', 'skipped'].includes(states[node.nodeKey]?.status));
  const current = currentIndex >= 0 ? nodes[currentIndex] : null;
  const completedCount = nodes.filter((node) => ['visited', 'skipped'].includes(states[node.nodeKey]?.status)).length;
  const progress = nodes.length ? Math.round((completedCount / nodes.length) * 100) : 0;
  if (!tripId || nodes.length === 0) return null;

  return <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" aria-label="旅中执行模式">
    <div className="flex items-center justify-between gap-2"><div><h3 className="flex items-center gap-2 text-sm font-black text-slate-800"><Flag className="h-4 w-4 text-orange-500" />旅中执行模式</h3><p className="mt-1 text-[11px] text-slate-500">状态会保存到当前行程，并作为后续重规划依据</p></div><button type="button" onClick={() => void load()} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100" title="同步执行状态"><RefreshCw className="h-3.5 w-3.5" /></button></div>
    {current && <div className="mt-3 rounded-xl border border-orange-200 bg-orange-50/70 p-3"><div className="flex items-center justify-between gap-2"><div><p className="text-[10px] font-bold text-orange-700">当前建议节点 · 第{Number(current.day) || 1}天</p><p className="mt-1 text-sm font-black text-slate-800">{current.name}</p></div><Clock3 className="h-5 w-5 text-orange-500" /></div><div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={busy} onClick={() => void update(current, 'in_progress')} className="inline-flex min-h-8 items-center gap-1 rounded-lg bg-orange-500 px-3 text-[11px] font-bold text-white hover:bg-orange-600 disabled:opacity-50"><TimerReset className="h-3.5 w-3.5" />开始</button><button type="button" disabled={busy} onClick={() => void update(current, 'visited')} className="inline-flex min-h-8 items-center gap-1 rounded-lg bg-emerald-600 px-3 text-[11px] font-bold text-white hover:bg-emerald-700 disabled:opacity-50"><CheckCircle2 className="h-3.5 w-3.5" />标记到访</button><button type="button" disabled={busy} onClick={() => void update(current, 'skipped')} className="inline-flex min-h-8 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-50"><SkipForward className="h-3.5 w-3.5" />跳过</button><button type="button" onClick={() => setDelayNode(current.nodeKey)} className="inline-flex min-h-8 items-center gap-1 rounded-lg border border-amber-200 bg-white px-3 text-[11px] font-bold text-amber-700 hover:bg-amber-50"><Clock3 className="h-3.5 w-3.5" />延误</button></div>{delayNode === current.nodeKey && <div className="mt-2 flex items-center gap-2"><input id="execution-delay" type="number" min={1} max={1440} defaultValue={30} className="h-8 w-20 rounded-lg border border-amber-200 bg-white px-2 text-xs" /><button type="button" disabled={busy} onClick={() => { const input = document.getElementById('execution-delay') as HTMLInputElement | null; void update(current, 'delayed', Math.max(1, Number(input?.value) || 30)); }} className="min-h-8 rounded-lg bg-amber-500 px-3 text-[11px] font-bold text-white">保存延误</button></div>}</div>}
    <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${progress}%` }} /></div>
    <div className="mt-2 grid gap-1.5 sm:grid-cols-2">{nodes.slice(0, 8).map((node) => <div key={node.nodeKey} className="flex items-center justify-between rounded-lg border border-slate-100 px-2.5 py-1.5 text-[10px]"><span className="min-w-0 truncate text-slate-600">D{Number(node.day) || 1} · {node.name}</span><span className={`ml-2 shrink-0 font-bold ${states[node.nodeKey]?.status === 'visited' ? 'text-emerald-600' : states[node.nodeKey]?.status === 'skipped' ? 'text-slate-400' : states[node.nodeKey]?.status === 'delayed' ? 'text-amber-600' : 'text-slate-400'}`}>{statusLabel[states[node.nodeKey]?.status || 'planned']}</span></div>)}</div>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-400"><span>已完成 {completedCount}/{nodes.length} · {progress}%</span><span className="flex items-center gap-2">{journey?.risk && <strong className="text-rose-600">有待确认风险</strong>}{journey?.budget?.level === 'over' && <strong className="text-rose-600">预算已超支</strong>}{current && onRequestReplan && <button type="button" onClick={() => onRequestReplan(current)} className="font-bold text-orange-600 hover:text-orange-700">仅重排剩余路线</button>}</span></div>
    {error && <p className="mt-2 text-[11px] font-bold text-rose-600">{error}</p>}
    {busy && <p className="mt-2 flex items-center gap-1 text-[10px] text-slate-400"><Loader2 className="h-3 w-3 animate-spin" />正在同步</p>}
  </section>;
}
