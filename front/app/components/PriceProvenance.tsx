'use client';

// 价格出处标签：紧挨着"预估花销"渲染，让用户一眼看出这个价**是从哪来的**。
//
// 背景（2026-10-07）：后端把门票接成了三条定向通道（官方文旅名录 / 文旅部 5A 名录 / 票务平台接口），
// 方案里也确实带回了票种名与来源 URL，但前端只显示「预估花销：¥30」——
// 用户看不出这是官方门票、票务平台的哪种票、还是网摘估算（"¥30"和"¥2999（其实是三日游）"看起来一样可信）。

import { ExternalLink, ShieldCheck, Ticket, TriangleAlert } from 'lucide-react';
import { priceProvenance, officialProfileOf, type PriceProvenance } from '../lib/priceProvenance';

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

export default PriceProvenanceChip;
