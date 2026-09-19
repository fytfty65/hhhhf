'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { BusFront, Hotel, Luggage, Route, RefreshCw, Sparkles, UtensilsCrossed, Layers3, Compass, ClipboardCheck } from 'lucide-react';
import { API_BASE, apiJson } from '../lib/utils';

type Props = { userId: string; tripId: string; destination: string; baseNodes?: any[]; budget?: number; days?: number; travelers?: number };

function CandidateMeta({ item }: { item: any }) {
  const confidence = typeof item?.confidence === 'number' ? `${Math.round(item.confidence * 100)}%` : '未知';
  const age = typeof item?.freshness_seconds === 'number' ? (item.freshness_seconds < 60 ? '刚刚' : `${Math.round(item.freshness_seconds / 60)} 分钟前`) : '未知';
  const source = item?.source?.provider || '未提供';
  const externalUrl = item?.extra?.booking_url || item?.extra?.deep_link || item?.extra?.url || item?.extra?.link;
  const externalLabel = item?.extra?.booking_label || '打开官方详情';
  return <div className="mt-2 space-y-1 border-t border-slate-100 pt-2 text-[10px] text-slate-400">
    <div className="flex flex-wrap gap-x-3 gap-y-1"><span>证据：{source}</span><span>时效：{age}</span><span>置信度：{confidence}</span><span>{item?.source?.estimated ? '估算' : '已核验'}</span></div>
    {Array.isArray(item?.evidence) && item.evidence.length > 0 && <p>依据：{item.evidence.slice(0, 3).map((e: any) => `${e.field}${e.estimated ? '（估算）' : ''}`).join(' · ')}</p>}
    {Array.isArray(item?.alternatives) && item.alternatives.length > 0 && <p>替代项：{item.alternatives.slice(0, 3).map((e: any) => e.name).join(' · ')}</p>}
    {typeof externalUrl === 'string' && /^https:\/\//i.test(externalUrl) && <a href={externalUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center rounded-md border border-orange-200 bg-orange-50 px-2 py-1 font-bold text-orange-700 hover:bg-orange-100">{externalLabel} ↗</a>}
  </div>;
}

export default function PreTripPlanningPanel({ userId, tripId, destination, baseNodes = [], budget = 0, days = 1, travelers = 1 }: Props) {
  const [tab, setTab] = useState<'destination' | 'transport' | 'lodging' | 'dining' | 'packing' | 'secondary' | 'pretrip' | 'joint'>('destination');
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [requirements, setRequirements] = useState('优先特色景点和人少、距离短的路线');
  const [lastGeneratedRequirements, setLastGeneratedRequirements] = useState('');
  const [quality, setQuality] = useState<any>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const context = useMemo(() => ({ user_id: userId, trip_id: tripId, destination, travelers, budget, currency: 'CNY', hard_constraints: { budget, travelers, days }, soft_preferences: {} }), [userId, tripId, destination, travelers, budget, days]);
  const track = useCallback(async (eventType: string, payload: Record<string, any> = {}) => {
    try { await apiJson(`${API_BASE}/api/v1/planning/events`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...context, event_type: eventType, payload }) }); } catch { /* analytics must not block planning */ }
  }, [context]);
  useEffect(() => {
    if (!tripId) return;
    void apiJson<any>(`${API_BASE}/api/v1/planning/quality?trip_id=${encodeURIComponent(tripId)}`).then((response) => setQuality(response?.data || response)).catch(() => undefined);
  }, [tripId]);
  const load = useCallback(async (nextTab = tab) => {
    if (!userId || !tripId || !destination) return;
    setBusy(true); setError('');
    try {
      const paths: Record<string, string> = {
        destination: '/api/v1/planning/destination/recommend',
        transport: '/api/v1/planning/transport/options',
        lodging: '/api/v1/planning/lodging/options',
        dining: '/api/v1/planning/dining/options',
        packing: '/api/v1/planning/packing/checklist',
        secondary: '/api/v1/planning/secondary',
        pretrip: '/api/v1/planning/pretrip/tasks',
        joint: '/api/v1/planning/joint',
      };
      if ((nextTab === 'secondary' || nextTab === 'joint') && data && requirements.trim() !== lastGeneratedRequirements.trim()) {
        void track('plan_edited', { module: nextTab, previous_requirements: lastGeneratedRequirements, requirements });
      }
      const body = nextTab === 'secondary' || nextTab === 'joint' ? { ...context, days, requirements, base_nodes: baseNodes } : context;
      const response = await apiJson<any>(`${API_BASE}${paths[nextTab]}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      setData(response?.data || response);
      if (nextTab === 'secondary' || nextTab === 'joint') setLastGeneratedRequirements(requirements);
      if (nextTab !== 'joint') void track('plan_generated', { module: nextTab, base_node_count: baseNodes.length, model_version: response?.data?.model?.version || response?.model?.version || 'builtin' });
      void apiJson<any>(`${API_BASE}/api/v1/planning/quality?trip_id=${encodeURIComponent(tripId)}`).then((nextQuality) => setQuality(nextQuality?.data || nextQuality)).catch(() => undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : '行前规划服务暂不可用');
    } finally { setBusy(false); }
  }, [tab, userId, tripId, destination, context, requirements, baseNodes, days, track, data, lastGeneratedRequirements]);

  const selectTab = (next: typeof tab) => { setTab(next); setData(null); setExpandedId(null); setError(''); void track('module_open', { module: next }); void load(next); };
  const tabs = [
    { id: 'destination' as const, label: '目的地推荐', icon: Compass },
    { id: 'transport' as const, label: '交通组合', icon: BusFront },
    { id: 'lodging' as const, label: '住宿匹配', icon: Hotel },
    { id: 'dining' as const, label: '餐饮推荐', icon: UtensilsCrossed },
    { id: 'packing' as const, label: '物品清单', icon: Luggage },
    { id: 'secondary' as const, label: '景区二次规划', icon: Route },
    { id: 'pretrip' as const, label: '行前任务', icon: ClipboardCheck },
    { id: 'joint' as const, label: '联合规划', icon: Layers3 },
  ];

  return <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" aria-label="行前规划工作台">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><h3 className="text-sm font-black text-slate-800 flex items-center gap-1.5"><Sparkles className="h-4 w-4 text-orange-500" /> 行前规划工作台</h3><p className="mt-0.5 text-[11px] text-slate-400">围绕当前行程补齐交通、住宿、装备与景区细节</p></div>
      <button type="button" onClick={() => void load()} disabled={busy || !tripId} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> 刷新</button>
    </div>
    <div className="mt-4 grid grid-cols-2 gap-2 border-y border-slate-100 py-3 sm:grid-cols-4 lg:grid-cols-8">
      {tabs.map(({ id, label, icon: Icon }) => <button type="button" key={id} onClick={() => selectTab(id)} className={`flex min-h-10 items-center justify-center gap-1.5 border-b-2 px-2 py-2 text-[11px] font-bold transition-colors ${tab === id ? 'border-orange-500 text-orange-700' : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800'}`}><Icon className="h-3.5 w-3.5" />{label}</button>)}
    </div>
    {(tab === 'secondary' || tab === 'joint') && <div className="mt-3 flex gap-2"><input value={requirements} onChange={e => setRequirements(e.target.value)} className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-xs" placeholder="例如：人少、少爬坡、优先特色店铺" /><button type="button" onClick={() => void load(tab)} disabled={busy} className="rounded-lg bg-slate-800 px-3 py-2 text-xs font-bold text-white disabled:opacity-50">{tab === 'joint' ? '生成联合方案' : '生成路线'}</button></div>}
    {error && <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs font-bold text-rose-600">{error}</p>}
    {quality?.funnel && <p className="mt-3 text-[10px] text-slate-400">规划质量 · 已生成 {quality.funnel.generated || 0} · 已编辑 {quality.funnel.edited || 0} · 已采纳 {quality.funnel.adopted || 0}{quality.training_ready ? ' · 样本已达训练门槛' : ''}</p>}
    {!data && !error && <p className="mt-4 text-center text-xs text-slate-400">点击上方模块加载建议</p>}
    {data?.options && <div className="mt-3 space-y-2">{data.options.map((item: any, index: number) => { const id = String(item.id || `${tab}-${index}`); const expanded = expandedId === id; return <article key={id} className="rounded-xl border border-slate-100 bg-slate-50/70 p-3"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><h4 className="text-xs font-black text-slate-800">{item.name}</h4><p className="mt-1 text-[11px] text-slate-500">{(item.reasons || []).join(' · ')}</p></div><div className="flex shrink-0 items-center gap-2"><span className="text-right text-xs font-black text-orange-600">{item.price ? `¥${item.price}` : item.extra?.estimated_price_range ? <>{item.extra.estimated_price_range}<small className="block text-[9px] font-normal text-slate-400">估算，待核验</small></> : `${Math.round((item.score || 0) * 100)}分`}</span><button type="button" onClick={() => setExpandedId(expanded ? null : id)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-bold text-slate-600 hover:border-orange-300 hover:text-orange-600">{expanded ? '收起' : '查看方案'}</button></div></div><div className="mt-2 flex flex-wrap gap-1">{(item.tags || []).map((tag: string) => <span key={tag} className="rounded bg-white px-1.5 py-0.5 text-[10px] text-slate-500">{tag}</span>)}</div>{expanded && <div className="mt-2 rounded-lg border border-orange-100 bg-white p-2 text-[11px] text-slate-600"><p>{item.description || item.detail || '该方案依据当前行程约束生成。'}</p>{(() => { const verifyUrl = item.amap_url || item.url || item.booking_url || `https://www.amap.com/search?query=${encodeURIComponent(String(item.name || ''))}`; return <a href={verifyUrl} target="_blank" rel="noreferrer" data-testid="candidate-verify-link" className="mt-1.5 inline-flex items-center gap-1 rounded-lg border border-orange-200 bg-orange-50 px-2 py-1 text-[11px] font-bold text-orange-700 hover:bg-orange-100">去核验这一个 ↗</a>; })()}{item.constraints && <p className="mt-1 text-slate-400">适用约束：{Object.entries(item.constraints).map(([key, value]) => `${key}=${String(value)}`).join(' · ')}</p>}</div>}<CandidateMeta item={item} /></article>; })}</div>}
    {data?.candidates && <div className="mt-3 space-y-2">{data.candidates.map((item: any, index: number) => { const id = String(item.id || `${tab}-candidate-${index}`); const expanded = expandedId === id; return <article key={id} className="rounded-xl border border-slate-100 bg-slate-50/70 p-3"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><h4 className="text-xs font-black text-slate-800">{item.name}</h4><p className="mt-1 text-[11px] text-slate-500">{(item.reasons || []).join(' · ')}</p></div><div className="flex shrink-0 items-center gap-2"><span className="text-xs font-black text-orange-600">{Math.round((item.score || 0) * 100)}分</span><button type="button" onClick={() => setExpandedId(expanded ? null : id)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-bold text-slate-600 hover:border-orange-300 hover:text-orange-600">{expanded ? '收起' : '查看方案'}</button></div></div>{expanded && <div className="mt-2 rounded-lg border border-orange-100 bg-white p-2 text-[11px] text-slate-600"><p>{item.description || item.detail || '候选方案将结合当前行程、预算和成员偏好进行核验。'}</p>{(() => { const verifyUrl = item.amap_url || item.url || item.booking_url || `https://www.amap.com/search?query=${encodeURIComponent(String(item.name || ''))}`; return <a href={verifyUrl} target="_blank" rel="noreferrer" data-testid="candidate-verify-link" className="mt-1.5 inline-flex items-center gap-1 rounded-lg border border-orange-200 bg-orange-50 px-2 py-1 text-[11px] font-bold text-orange-700 hover:bg-orange-100">去核验这一个 ↗</a>; })()}</div>}<CandidateMeta item={item} /></article>; })}</div>}
    {data?.items && <div className="mt-3 space-y-2"><div className="flex flex-wrap gap-1.5 text-[10px] text-slate-400">{data.linkage && Object.entries(data.linkage).filter(([, value]) => value === true).map(([key]) => <span key={key} className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">已联动 {key}</span>)}</div><div className="grid gap-2 sm:grid-cols-2">{data.items.map((item: any) => <label key={item.id} className="flex items-start gap-2 rounded-lg border border-slate-100 px-3 py-2 text-xs text-slate-700"><input type="checkbox" className="mt-0.5 h-4 w-4 rounded border-slate-300" /> <span className="flex-1"><span className="block">{item.name}</span><span className="mt-0.5 block text-[10px] text-slate-400">{item.priority} · {item.category} · {item.reason}</span></span>{item.source?.estimated && <span className="text-[10px] text-slate-400">估算</span>}</label>)}</div></div>}
    {data?.tasks && <div className="mt-3 grid gap-2 sm:grid-cols-2">{data.tasks.map((item: any) => <label key={item.id} className="flex items-start gap-2 rounded-lg border border-slate-100 px-3 py-2 text-xs text-slate-700"><input type="checkbox" className="mt-0.5 h-4 w-4 rounded border-slate-300" /> <span className="flex-1"><span className="block font-bold">{item.title}</span><span className="mt-0.5 block text-[10px] text-slate-400">{item.category} · 截止 {item.due_date || '出发前'} · {item.source?.estimated ? '规则估算' : '供应商'}</span></span></label>)}</div>}
    {data?.route && <div className="mt-3 space-y-2">{data.route.map((item: any, index: number) => <div key={`${item.name}-${index}`} className="flex items-center gap-3 rounded-lg border border-slate-100 px-3 py-2"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-orange-100 text-[11px] font-black text-orange-700">{index + 1}</span><div><p className="text-xs font-bold text-slate-700">{item.name}</p><p className="text-[10px] text-slate-400">客流 {item.crowd} · 建议停留 {item.duration_minutes} 分钟</p></div></div>)}</div>}
    {data?.plan && <div className="mt-3 rounded-xl border border-orange-100 bg-orange-50/60 p-3"><div className="flex items-start justify-between gap-2"><div><h4 className="text-xs font-black text-slate-800">{data.plan.name}</h4><p className="mt-1 text-[11px] text-slate-500">保留 {data.plan.preserved_nodes} 个原路线节点 · {data.plan.days} 天 · {data.plan.travelers} 人</p>{(() => { const nodes = data.plan.kept_nodes || data.plan.nodes || data.plan.route || []; return nodes.length > 0 ? <div className="mt-2 rounded-lg border border-orange-100 bg-white p-2" data-testid="joint-plan-nodes"><p className="text-[11px] font-bold text-slate-700">这套方案具体排的是：</p><ol className="mt-1 space-y-0.5">{nodes.slice(0, 8).map((n: any, i: number) => <li key={i} className="text-[11px] text-slate-600">Day {n?.day || '?'} · {n?.name || String(n)}</li>)}</ol>{nodes.length > 8 && <p className="text-[10px] text-slate-400">…还有 {nodes.length - 8} 个</p>}</div> : <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] leading-relaxed text-amber-700">这套方案目前只有汇总指标（保留 {data.plan.preserved_nodes} 个原路线节点），后端还没下发具体节点清单，所以看不出到底怎么排的。</p>; })()}{Array.isArray(data.alternatives) && data.alternatives.length > 0 && <div className="mt-2 rounded-lg border border-orange-100 bg-white p-2" data-testid="joint-plan-alternatives"><p className="text-[11px] font-bold text-slate-700">三套备选差在哪（当前选中：{data.plan.name}）</p><div className="mt-1 space-y-1">{data.alternatives.slice(0, 3).map((alt: any, i: number) => <div key={i} className="flex flex-wrap items-baseline gap-x-2 text-[11px] text-slate-600"><strong className={alt.name === data.plan.name ? 'text-emerald-600' : 'text-slate-700'}>{alt.name}</strong><span>¥{Math.round(alt.estimated_cost || 0)}</span><span>约 {Math.round((alt.estimated_duration_minutes || 0) / 60)} 小时</span><span>碳排 {alt.carbon_kg ?? '—'} kg</span><span>满意度 {Math.round((alt.satisfaction || 0) * 100)}%</span>{Array.isArray(alt.tradeoffs) && alt.tradeoffs.length > 0 && <span className="text-slate-400">（{alt.tradeoffs.join('、')}）</span>}</div>)}</div></div>}</div><span className="text-right text-sm font-black text-orange-700">¥{Math.round(data.plan.estimated_cost).toLocaleString()}<small className="ml-1 block text-[9px] font-normal text-slate-400">{data.plan.estimated ? '估算，待供应商确认' : '供应商数据'}</small></span></div><p className={`mt-2 text-[11px] font-bold ${data.constraints?.hard_satisfied ? 'text-emerald-600' : 'text-rose-600'}`}>{data.constraints?.hard_satisfied ? '硬约束已满足' : `需要调整：${(data.constraints?.violations || []).join('、')}`}</p><p className="mt-1 text-[11px] text-slate-500">成员最低满意度 {Math.round((data.fairness?.minimum_satisfaction || 0) * 100)}% · 满意度方差 {(data.fairness?.satisfaction_variance || 0).toFixed(3)}</p>{data.fairness?.threshold_exceeded && <p className="mt-1 text-[11px] font-bold text-rose-600">成员公平阈值超标，建议重新选路</p>}{data.ranking_calibration && <p className="mt-1 text-[10px] text-slate-400">{data.ranking_calibration.season}季 · 季节系数 {Number(data.ranking_calibration.season_factor || 1).toFixed(2)} · 客流系数 {Number(data.ranking_calibration.crowd_factor || 1).toFixed(2)}</p>}{data.model && <p className="mt-1 text-[10px] text-slate-400">规划版本 {data.model.version} · {data.model.status === 'canary' ? '灰度流量' : data.model.status === 'fallback' ? '内置回退' : '线上版本'}</p>}{data.signals && <p className="mt-1 text-[10px] text-slate-400">实时信号：天气 {data.signals.weather?.available ? '已接入' : '估算'} · 客流 {data.signals.crowd?.available ? '已接入' : '估算'}</p>}{data.explain && <p className="mt-2 text-[10px] text-slate-400">{data.explain.join(' · ')}</p>}{data.plan_id && <div className="mt-3 flex gap-2"><button type="button" onClick={() => void track('plan_adopted', { plan_id: data.plan_id, module: 'joint', bandit_arm_id: data.bandit?.arm_id || data.model?.version })} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-[11px] font-bold text-white hover:bg-emerald-700">采用方案</button><button type="button" onClick={() => void track('plan_rejected', { plan_id: data.plan_id, module: 'joint', bandit_arm_id: data.bandit?.arm_id || data.model?.version })} className="rounded-lg border border-slate-200 px-3 py-1.5 text-[11px] font-bold text-slate-600 hover:bg-white">暂不采用</button></div>}</div>}
  </section>;
}
