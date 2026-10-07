'use client';

// 价格出处标签：紧挨着"预估花销"渲染，让用户一眼看出这个价**是从哪来的**。
//
// 背景（2026-10-07）：后端把门票接成了三条定向通道（官方文旅名录 / 文旅部 5A 名录 / 票务平台接口），
// 方案里也确实带回了票种名与来源 URL，但前端只显示「预估花销：¥30」——
// 用户看不出这是官方门票、票务平台的哪种票、还是网摘估算（"¥30"和"¥2999（其实是三日游）"看起来一样可信）。

import { ExternalLink, ShieldCheck, Ticket, TriangleAlert, Info } from 'lucide-react';
import { priceProvenance, officialProfileOf, type PriceProvenance } from '../lib/priceProvenance';
import { nodeAnnotations, officialDetailFor, dayNotices } from '../lib/planAnnotations';

const KIND_STYLE: Record<string, string> = {
  official: 'bg-emerald-50 text-emerald-700 border-emerald-200/80',
  supplier: 'bg-sky-50 text-sky-700 border-sky-200/80',
  platform: 'bg-amber-50 text-amber-700 border-amber-200/80',
  estimate: 'bg-slate-50 text-slate-600 border-slate-200',
  unknown: 'bg-rose-50 text-rose-700 border-rose-200/80',
};

function KindIcon({ kind }: { kind: string }) {
  if (kind === 'official' || kind === 'supplier') return <ShieldCheck className="w-3 h-3" />;
  if (kind === 'platform') return <Ticket className="w-3 h-3" />;
  if (kind === 'estimate') return <TriangleAlert className="w-3 h-3" />;
  return <TriangleAlert className="w-3 h-3" />;
}

export function PriceProvenanceChip({ node, plan }: { node: any; plan?: any }) {
  const info: PriceProvenance | null = priceProvenance(node);
  // 官方入口优先取节点上已铺平的字段，其次回退到方案级 official_profiles
  const inline = node && (node.official_booking || node.official_site)
    ? { booking: String(node.official_booking || ''), site: String(node.official_site || ''), name: String(node.official_name || '') }
    : officialProfileOf(plan, node?.name);
  const profile = inline;
  if (!info && !profile) return null;

  const bookingUrl = profile?.booking || profile?.site || '';

  return (
    <span className="flex items-center gap-1.5 flex-wrap">
      {info && (
        <span
          data-testid="price-provenance"
          title={info.detail ? `${info.label}：${info.detail}` : info.label}
          className={`flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-bold border whitespace-nowrap ${KIND_STYLE[info.kind] || KIND_STYLE.estimate}`}
        >
          <KindIcon kind={info.kind} />
          <span className="whitespace-nowrap">{info.label}</span>
          {info.detail && (
            <span className="font-medium opacity-80 max-w-[200px] truncate">· {info.detail}</span>
          )}
          {info.url && (
            <a
              href={info.url}
              target="_blank"
              rel="noreferrer noopener"
              onClick={(event) => event.stopPropagation()}
              className="inline-flex items-center gap-0.5 whitespace-nowrap underline decoration-dotted hover:opacity-80"
            >
              来源<ExternalLink className="w-3 h-3" />
            </a>
          )}
        </span>
      )}
      {bookingUrl && (
        <a
          data-testid="official-booking"
          href={bookingUrl}
          target="_blank"
          rel="noreferrer noopener"
          onClick={(event) => event.stopPropagation()}
          title={profile?.name ? `官方登记：${profile.name}` : '官方预约入口'}
          className="flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-bold border whitespace-nowrap bg-indigo-50 text-indigo-700 border-indigo-200/80 hover:bg-indigo-100"
        >
          <Ticket className="w-3 h-3" />
          官方预约
          <ExternalLink className="w-3 h-3" />
        </a>
      )}
    </span>
  );
}

/** 节点上的**台账标记**：换过酒店/餐馆、被补进来、被挪过来、按地理重排换过天 */
export function PlanAnnotationList({ plan, nodeName }: { plan?: any; nodeName?: unknown }) {
  const annotations = nodeAnnotations(plan, nodeName);
  if (!annotations.length) return null;
  return (
    <div className="mb-3 flex flex-col gap-1.5" data-testid="plan-annotations">
      {annotations.map((item, index) => (
        <div
          key={`${item.kind}-${index}`}
          data-annotation-kind={item.kind}
          className="flex items-start gap-1.5 text-[11px] font-medium text-amber-800 bg-amber-50/80 border border-amber-200/80 px-2.5 py-1.5 rounded-xl"
        >
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-amber-600" />
          <span className="leading-relaxed">{item.text}</span>
        </div>
      ))}
    </div>
  );
}

/** 官方登记资料（文旅部名录）：官方名称/地址/简介 —— 补上"地点资料"这一栏 */
export function OfficialDetailBlock({ plan, nodeName }: { plan?: any; nodeName?: unknown }) {
  const detail = officialDetailFor(plan, nodeName);
  if (!detail) return null;
  return (
    <div
      data-testid="official-detail"
      className="mb-3 p-3 rounded-2xl bg-indigo-50/70 border border-indigo-200/70 text-[11px] leading-relaxed"
    >
      <div className="font-bold text-indigo-800 mb-1 flex items-center gap-1.5">
        <ShieldCheck className="w-3.5 h-3.5" />
        官方登记（文旅部 A 级景区名录）
        {detail.name && detail.name !== String(nodeName ?? '') ? <span className="font-medium opacity-80">· {detail.name}</span> : null}
      </div>
      {detail.address && (
        <div className="text-slate-600 flex items-start gap-1.5">
          <MapPinIcon />
          <span>{detail.address}</span>
        </div>
      )}
      {detail.intro && <p className="text-slate-700 mt-1">{detail.intro}</p>}
    </div>
  );
}

function MapPinIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-3 h-3 mt-0.5 shrink-0 text-indigo-500" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 21s-7-6.1-7-11a7 7 0 1 1 14 0c0 4.9-7 11-7 11z" />
      <circle cx="12" cy="10" r="2.5" />
    </svg>
  );
}

/** 当天提醒：缺餐（"宁可不塞"）、补过餐、分天被重排过 */
export function DayNoticeList({ plan, day }: { plan?: any; day?: number }) {
  const notices = dayNotices(plan)[Number(day)] || [];
  if (!notices.length) return null;
  return (
    <div className="mt-2 flex flex-col gap-1.5" data-testid="day-notices">
      {notices.map((notice, index) => (
        <div
          key={`${notice.kind}-${index}`}
          data-notice-kind={notice.kind}
          className={`flex items-start gap-1.5 text-[11px] font-bold px-3 py-1.5 rounded-xl border ${
            notice.kind === 'meal_missing'
              ? 'bg-rose-50 text-rose-700 border-rose-200/80'
              : notice.kind === 'long_day'
                ? 'bg-amber-50 text-amber-800 border-amber-200/80'
                : 'bg-slate-50 text-slate-600 border-slate-200'
          }`}
        >
          <TriangleAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span className="leading-relaxed">{notice.text}</span>
        </div>
      ))}
    </div>
  );
}

export default PriceProvenanceChip;
