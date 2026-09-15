'use client';

import { useEffect, useState } from 'react';
import { ExternalLink, Hotel, Info, Plane, RefreshCw, ShieldCheck, Ticket, Train, X } from 'lucide-react';
import { API_BASE, apiJson } from '../lib/utils';

type ProviderStatus = {
  configured?: boolean;
  authenticated?: boolean;
  provider?: string;
  endpoint_count?: number;
};

type BookingHubProps = {
  open: boolean;
  destination?: string;
  origin?: string;
  date?: string;
  onClose: () => void;
};

const officialLinks = [
  { key: 'train', label: '铁路票', icon: Train, href: 'https://kyfw.12306.cn/otn/leftTicket/init', note: '12306 官方购票，价格和余票以官方页面为准' },
  { key: 'flight', label: '航班', icon: Plane, href: 'https://www.csair.com/cn/', note: '前往航空公司官网查询与购票' },
  { key: 'hotel', label: '住宿', icon: Hotel, href: 'https://www.amap.com/', note: '通过地图或住宿方官方页面完成预订' },
  { key: 'ticket', label: '门票', icon: Ticket, href: 'https://www.amap.com/', note: '优先使用景区官方售票入口' },
];

export default function BookingHub({ open, destination, origin, date, onClose }: BookingHubProps) {
  const [health, setHealth] = useState<Record<string, ProviderStatus> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const trackChannel = async (channel: string, targetURL: string) => {
    try {
      await apiJson(`${API_BASE}/api/v1/planning/channels/click`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel, destination: destination || '', target_url: targetURL }),
      });
    } catch {
      // Channel navigation must remain usable when analytics is unavailable.
    }
  };

  const loadHealth = async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await apiJson<{ data?: { providers?: Record<string, ProviderStatus> }; providers?: Record<string, ProviderStatus> }>(
        `${API_BASE}/api/v1/planning/providers/health`,
      );
      setHealth(payload.data?.providers || payload.providers || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '供应商状态暂时不可用');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void loadHealth();
  }, [open]);

  if (!open) return null;
  const query = [origin && `from=${encodeURIComponent(origin)}`, destination && `to=${encodeURIComponent(destination)}`, date && `date=${encodeURIComponent(date)}`].filter(Boolean).join('&');

  return (
    <div className="fixed inset-0 z-[160] flex items-center justify-center bg-slate-950/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <section role="dialog" aria-modal="true" aria-labelledby="booking-hub-title" onClick={(event) => event.stopPropagation()} className="w-full max-w-2xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-5 py-4">
          <div className="flex items-center gap-3"><div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-500 text-white"><Ticket className="h-5 w-5" /></div><div><h2 id="booking-hub-title" className="text-base font-black text-slate-900">预订与官方渠道</h2><p className="text-xs text-slate-500">OmniRoute 负责决策与导流，交易将在第三方完成</p></div></div>
          <button onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-200" aria-label="关闭"><X className="h-4 w-4" /></button>
        </header>
        <div className="space-y-5 p-5">
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-900"><Info className="mr-1 inline h-3.5 w-3.5" />当前账号无企业预订资质，平台不会收集支付信息或承诺出票。请在官方页面核验价格、余票、退改规则后完成交易。</div>
          <div className="grid gap-3 sm:grid-cols-2">
            {officialLinks.map(({ key, label, icon: Icon, href, note }) => {
              const status = health?.[key === 'hotel' ? 'lodging' : key === 'ticket' ? 'scenic' : 'transport'];
              const target = key === 'train' && query ? `${href}?${query}` : href;
              return <a key={key} href={target} target="_blank" rel="noopener noreferrer" onClick={() => { void trackChannel(key, target); }} className="group rounded-xl border border-slate-200 bg-white p-4 transition hover:border-orange-300 hover:bg-orange-50/40"><div className="flex items-center justify-between"><span className="flex items-center gap-2 text-sm font-black text-slate-800"><Icon className="h-4 w-4 text-orange-500" />{label}</span><ExternalLink className="h-4 w-4 text-slate-400 transition group-hover:text-orange-600" /></div><p className="mt-2 text-[11px] leading-5 text-slate-500">{note}</p><p className="mt-2 text-[10px] font-bold text-slate-400">{status?.configured ? `已配置 · ${status.provider || '供应商'}` : '平台未配置交易供应商 · 官方入口可用'}</p></a>;
            })}
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4"><div className="mb-3 flex items-center justify-between"><h3 className="flex items-center gap-2 text-xs font-black text-slate-700"><ShieldCheck className="h-4 w-4 text-emerald-600" />数据供应商状态</h3><button onClick={() => void loadHealth()} disabled={loading} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-white" title="刷新状态"><RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /></button></div>{error && <p className="text-xs text-amber-700">{error}</p>}{!error && <div className="grid grid-cols-2 gap-2 text-[11px]">{['transport', 'lodging', 'scenic', 'weather'].map((key) => { const status = health?.[key]; return <div key={key} className="flex items-center justify-between rounded-lg bg-white px-3 py-2"><span className="text-slate-500">{key === 'transport' ? '交通' : key === 'lodging' ? '住宿' : key === 'scenic' ? '景点' : '天气'}</span><span className={status?.configured ? 'font-bold text-emerald-700' : 'font-bold text-slate-400'}>{status?.configured ? '已配置' : '未配置'}</span></div>; })}</div>}</div>
        </div>
      </section>
    </div>
  );
}
