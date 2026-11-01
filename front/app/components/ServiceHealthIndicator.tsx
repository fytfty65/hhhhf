'use client';

/**
 * 大厅顶部「服务状态」。
 *
 * 只陈述检测到的事实：网关是否响应、AI 规划是否可达、供应商是「已配置」还是
 * 「探测可达」。点开后才按需触发真实探测（会访问外部供应商，慢且可能计费）。
 */

import { useEffect, useRef, useState } from 'react';
import { ChevronDown, RefreshCw } from 'lucide-react';
import {
  HEALTH_STATE_LABELS,
  loadServiceHealth,
  summarizeHealth,
  type HealthState,
  type ServiceHealthSnapshot,
} from '../lib/serviceHealth';

const TONE_DOT: Record<'ok' | 'warn' | 'bad', string> = {
  ok: 'bg-emerald-500',
  warn: 'bg-amber-500',
  bad: 'bg-rose-500',
};

const STATE_DOT: Record<HealthState, string> = {
  available: 'bg-emerald-500',
  configured: 'bg-sky-500',
  unavailable: 'bg-rose-500',
  unknown: 'bg-slate-400',
};

export default function ServiceHealthIndicator({ className = '' }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<ServiceHealthSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [probed, setProbed] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const refresh = async (withProbe = false) => {
    setLoading(true);
    try {
      setSnapshot(await loadServiceHealth({ probe: withProbe }));
      if (withProbe) setProbed(true);
    } finally {
      setLoading(false);
    }
  };

  // 只在首次挂载时检测：服务状态不是高频数据，不需要轮询。
  useEffect(() => {
    refresh(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  const summary = summarizeHealth(snapshot);

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <button
        type="button"
        data-testid="service-health"
        aria-expanded={open}
        aria-label={`服务状态：${summary.text}`}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-600 shadow-sm transition-colors hover:border-orange-200 hover:text-orange-600"
      >
        <span className={`h-2 w-2 rounded-full ${TONE_DOT[summary.tone]}`} />
        <span className="hidden sm:inline">{summary.text}</span>
        <span className="sm:hidden">服务状态</span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-full z-40 mt-2 w-[min(20rem,calc(100vw-2rem))] rounded-2xl border border-slate-200 bg-white p-3 text-left shadow-xl">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-black text-slate-700">服务状态</p>
            <button
              type="button"
              onClick={() => refresh(probed)}
              disabled={loading}
              className="flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-bold text-slate-500 hover:bg-slate-50 disabled:opacity-60"
            >
              <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} /> 重新检测
            </button>
          </div>

          <ul className="space-y-1.5">
            {snapshot?.rows.map((row) => (
              <li key={row.key} className="flex items-start gap-2 rounded-lg bg-slate-50 px-2.5 py-1.5">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${STATE_DOT[row.state]}`} />
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-bold text-slate-700">{row.label}</span>
                    <span className="text-[11px] font-semibold text-slate-500">{HEALTH_STATE_LABELS[row.state]}</span>
                  </div>
                  <p className="mt-0.5 text-[11px] leading-snug text-slate-500">{row.detail}</p>
                </div>
              </li>
            ))}
            {!snapshot && (
              <li className="px-2.5 py-2 text-[11px] font-semibold text-slate-500">正在检测…</li>
            )}
          </ul>

          <button
            type="button"
            data-testid="service-health-probe"
            onClick={() => refresh(true)}
            disabled={loading || !snapshot?.gatewayOnline}
            className="mt-2 w-full rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-[11px] font-bold text-orange-700 transition-colors hover:bg-orange-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {probed ? '重新探测供应商可用性' : '检测供应商可用性（会访问外部服务）'}
          </button>
          <p className="mt-1.5 text-[10px] leading-snug text-slate-400">
            「已配置」只代表填了密钥；「可用」表示本次探测真的连上了。未检测时不代表可用。
          </p>
        </div>
      )}
    </div>
  );
}
