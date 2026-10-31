'use client';

// 价格出处标签：紧挨着"预估花销"渲染，让用户一眼看出这个价**是从哪来的**。
//
// 背景（2026-10-07）：后端把门票接成了三条定向通道（官方文旅名录 / 文旅部 5A 名录 / 票务平台接口），
// 方案里也确实带回了票种名与来源 URL，但前端只显示「预估花销：¥30」——
// 用户看不出这是官方门票、票务平台的哪种票、还是网摘估算（"¥30"和"¥2999（其实是三日游）"看起来一样可信）。

import { ExternalLink, ShieldCheck, Ticket, TriangleAlert, Info, Clock } from 'lucide-react';
import { priceProvenance, officialProfileOf, type PriceProvenance } from '../lib/priceProvenance';
import { nodeAnnotations, officialDetailFor, dayNotices } from '../lib/planAnnotations';
import { trustOf, trustSummaryOf, TRUST_STYLES, type TrustState } from '../lib/factTrust';

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

/** 事实可信度徽章（P3）：已核实 / 估算 / 信息较旧 / 尚未核实
 *
 * 为什么要单独一个组件：后端已经给每条事实标了四态，但用户看到的仍然只是"¥30"。
 * 这个徽章把"这条信息到底可不可信、该不该去官方渠道再确认"直接摆在数字旁边。
 */
export function TrustBadge({ node }: { node: any; factKind?: string }) {
  const view = trustOf(node);
  if (!view) return null;
  const style = TRUST_STYLES[view.state as TrustState] || TRUST_STYLES.unknown;
  return (
    <span
      data-testid="trust-badge"
      data-trust={view.state}
      title={[view.message, view.action].filter(Boolean).join('｜')}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-bold border whitespace-nowrap ${style}`}
    >
      {view.state === 'verified' ? (
        <ShieldCheck className="w-3 h-3" />
      ) : view.state === 'stale' ? (
        <Clock className="w-3 h-3" />
      ) : (
        <TriangleAlert className="w-3 h-3" />
      )}
      <span className="whitespace-nowrap">{view.label}</span>
      <span className="font-medium opacity-80 whitespace-nowrap">· {view.sourceLabel}</span>
    </span>
  );
}

/** 一份方案的"信息可信度"总览：四态各有多少条，最差的一档是什么 */
export function TrustSummaryBar({ plan }: { plan?: any }) {
  const nodes = (plan && typeof plan === 'object' && Array.isArray(plan.route)) ? plan.route : [];
  const summary = trustSummaryOf(nodes);
  if (!summary.worst) return null;
  const order: TrustState[] = ['verified', 'estimated', 'stale', 'unknown'];
  const present = order.filter((state) => summary.counts[state] > 0);
  return (
    <div
      data-testid="trust-summary"
      data-worst={summary.worst}
      className="flex items-center gap-2 flex-wrap text-[11px] font-bold"
    >
      <span className="text-slate-500">信息可信度</span>
      {present.map((state) => (
        <span
          key={state}
          data-trust-count={state}
          className={`px-2 py-0.5 rounded-lg border whitespace-nowrap ${TRUST_STYLES[state]}`}
        >
          {state === 'verified'
            ? '已核实'
            : state === 'estimated'
              ? '估算'
              : state === 'stale'
                ? '信息较旧'
                : '尚未核实'}{' '}
          {summary.counts[state]}
        </span>
      ))}
    </div>
  );
}

/** 节点上的**台账标记**：换过酒店/餐馆、被补进来、被挪过来、按地理重排换过天 */
export function PlanAnnotationList({ plan, nodeName }: { plan?: any; nodeName?: unknown }) {  const annotations = nodeAnnotations(plan, nodeName);
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
        {detail.intro || detail.address ? '官方登记（文旅部 A 级景区名录）' : '官方渠道'}
        {detail.name && detail.name !== String(nodeName ?? '') ? <span className="font-medium opacity-80">· {detail.name}</span> : null}
      </div>
      {detail.address && (
        <div className="text-slate-600 flex items-start gap-1.5">
          <MapPinIcon />
          <span>{detail.address}</span>
        </div>
      )}
      {detail.intro && <p className="text-slate-700 mt-1">{detail.intro}</p>}
      {detail.miniprogram && (
        <div className="text-slate-600 mt-1 flex items-start gap-1.5" data-testid="official-miniprogram">
          <Ticket className="w-3 h-3 mt-0.5 shrink-0 text-indigo-500" />
          <span>
            官方购票：微信小程序「<span className="font-bold">{detail.miniprogram}</span>」
            {detail.note ? <span className="opacity-80"> · {detail.note}</span> : null}
          </span>
        </div>
      )}
      {/* 票价可得性：本机拿不到官方价时必须说清"为什么"（只在小程序内 / 该源只有优惠政策） */}
      {detail.priceStatus && detail.priceStatus !== 'verified_official_price' && (
        <div className="text-amber-700 mt-1 flex items-start gap-1.5" data-testid="official-price-status">
          <TriangleAlert className="w-3 h-3 mt-0.5 shrink-0" />
          <span>{detail.priceStatusNote || '官方票价本机未取到（详见官方渠道）'}</span>
        </div>
      )}
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
