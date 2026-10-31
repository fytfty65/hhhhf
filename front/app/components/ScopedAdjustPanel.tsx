'use client';

import { SCOPED_CATEGORY_LABELS, describeScopedReport, scopedScopeLabel } from '../lib/scopedReplan';
import type { ScopedCategory, ScopedReport, ScopedScope } from '../lib/scopedReplan';

/**
 * 「只调整一部分」入口：只重算某一天 / 只替换交通住宿餐饮。
 *
 * 这里只负责把用户的选择变成一次明确的局部调整请求，并回显结果；
 * 是否真的只动了这一部分，由 lib/scopedReplan 逐天比对后如实说明，
 * 界面不替后端承诺"其它安排没动"。
 */
export default function ScopedAdjustPanel({
  days,
  pending,
  report,
  busy,
  onAdjust,
}: {
  days: number;
  pending: ScopedScope | null;
  report: ScopedReport | null;
  busy: boolean;
  onAdjust: (scope: ScopedScope) => void;
}) {
  const dayOptions = Array.from({ length: Math.max(1, days) }, (_, index) => index + 1);
  const categories = Object.keys(SCOPED_CATEGORY_LABELS) as ScopedCategory[];
  const described = report ? describeScopedReport(report) : null;
  const chipClass = 'min-h-8 rounded-lg border border-stone-200 bg-white px-2.5 text-[11px] font-bold text-stone-700 transition-colors hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50';

  return (
    <section
      data-testid="scoped-adjust"
      className="mt-3 rounded-2xl border border-stone-200 bg-white p-3 shadow-[0_8px_30px_rgba(67,45,25,0.06)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-black text-stone-800">只调整一部分</h3>
          <p className="mt-0.5 text-[11px] leading-relaxed text-stone-500">其它安排先按原样保留；重排后会把实际改到的位置如实报给你。</p>
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        <span className="w-16 shrink-0 text-[10px] font-bold text-stone-400">只重算</span>
        {dayOptions.map((day) => (
          <button
            key={`day-${day}`}
            type="button"
            data-testid={`scoped-adjust-day-${day}`}
            disabled={busy}
            onClick={() => onAdjust({ kind: 'day', day })}
            className={chipClass}
          >
            第 {day} 天
          </button>
        ))}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <span className="w-16 shrink-0 text-[10px] font-bold text-stone-400">只替换</span>
        {categories.map((category) => (
          <button
            key={category}
            type="button"
            data-testid={`scoped-adjust-category-${category}`}
            disabled={busy}
            onClick={() => onAdjust({ kind: 'category', category })}
            className={chipClass}
          >
            {SCOPED_CATEGORY_LABELS[category]}
          </button>
        ))}
      </div>

      {pending && (
        <p data-testid="scoped-adjust-pending" className="mt-2 text-[11px] font-bold text-orange-700">
          正在按「只改{scopedScopeLabel(pending)}」重排，其它部分先按原样保留…
        </p>
      )}
      {!pending && described && (
        <p
          data-testid="scoped-adjust-report"
          data-tone={described.tone}
          className={`mt-2 text-[11px] font-bold leading-relaxed ${described.tone === 'ok' ? 'text-emerald-700' : 'text-amber-700'}`}
        >
          {described.text}
        </p>
      )}
    </section>
  );
}
