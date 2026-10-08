'use client';

/**
 * 文化背景 / 无障碍信息面板（P1 的**渲染**端）。
 *
 * 为什么必须有这个组件：后端把文化与无障碍提案放进了 payload，
 * 但前端此前只把 `specialist_runtime` 发给审计接口，**用户一个字段也看不到**
 * —— 按项目一贯口径，"没渲染出来的已做等于没做"。
 *
 * 两块内容的口径完全不同，所以分开显示：
 *
 * ① 文化：出处、复核日期都摆出来，并明说来源是**项目内知识库**（不是官方发布）。
 *    查不到条目的节点**照样列出来**，但写"知识库里没有该地点的条目，未提供背景说明"
 *    —— 让用户知道"不是漏了，是我们没有可靠来源，宁可不写"。
 *
 * ② 无障碍：核验不到就**明确写"未核实"**，并给出该向景区确认的四项。
 *    绝不出现"无障碍设施齐全"这类没有依据的断言。
 */

import { BookOpen, ExternalLink, Accessibility } from 'lucide-react';
import { TRUST_STYLES } from '../lib/factTrust';

type CultureEntry = {
  name: string;
  background?: string;
  etiquette?: string;
  recommended_stay?: string;
  sources?: Array<{ label?: string; url?: string }>;
  reviewed_at?: string;
  source_type?: string;
  note?: string;
};

type AccessEntry = {
  name: string;
  verified?: boolean;
  status_text?: string;
  facilities?: Record<string, string>;
  checklist?: string[];
  note?: string;
};

export function CulturePanel({ proposals }: { proposals?: any[] }) {
  const proposal = (proposals || []).find((item: any) => item?.agent_id === 'culture-agent');
  const entries: CultureEntry[] = Array.isArray(proposal?.candidates) ? proposal.candidates : [];
  const sourced = entries.filter((item) => (item.sources || []).length > 0);
  if (!proposal || entries.length === 0) return null;

  return (
    <section
      data-testid="culture-panel"
      data-sourced-count={sourced.length}
      className="mb-6 rounded-2xl border border-amber-200 bg-amber-50/50 p-4"
    >
      <div className="flex items-center gap-2 mb-3">
        <BookOpen className="w-4 h-4 text-amber-700" />
        <h4 className="text-sm font-black text-amber-900">文化背景与当地礼仪</h4>
        {/* 来源性质要说清：知识库不是官方发布，别让用户以为是官方口径 */}
        <span className="text-[11px] text-amber-700 bg-white/70 border border-amber-200 rounded px-2 py-0.5">
          来源：项目内可信知识库（非官方发布）
        </span>
        <span className="text-[11px] text-amber-700">
          命中 {sourced.length}/{entries.length}
        </span>
      </div>

      <div className="space-y-3">
        {entries.map((entry, index) => {
          const hasBackground = Boolean(entry.background);
          return (
            <div
              key={`culture-${index}`}
              data-testid="culture-entry"
              data-has-background={hasBackground ? 'true' : 'false'}
              className="bg-white/80 rounded-xl border border-amber-100 p-3"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-bold text-slate-800">{entry.name}</span>
                {entry.recommended_stay && (
                  <span className="text-[11px] text-slate-600 bg-slate-100 rounded px-2 py-0.5">
                    建议停留：{entry.recommended_stay}
                  </span>
                )}
                {entry.reviewed_at && (
                  <span className="text-[11px] text-slate-500">复核于 {entry.reviewed_at}</span>
                )}
              </div>

              {hasBackground ? (
                <>
                  <p className="text-xs text-slate-700 leading-relaxed mt-2">{entry.background}</p>
                  {entry.etiquette && (
                    <p className="text-xs text-slate-600 leading-relaxed mt-1">
                      <span className="font-bold text-slate-700">当地礼仪：</span>
                      {entry.etiquette}
                    </p>
                  )}
                  {(entry.sources || []).length > 0 && (
                    <div className="flex flex-wrap items-center gap-2 mt-2">
                      <span className="text-[11px] text-slate-500">信息来源：</span>
                      {(entry.sources || []).map((source, sourceIndex) =>
                        source.url ? (
                          <a
                            key={`src-${index}-${sourceIndex}`}
                            href={source.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-[11px] text-blue-600 hover:underline inline-flex items-center gap-0.5"
                          >
                            {source.label || source.url}
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        ) : (
                          <span key={`src-${index}-${sourceIndex}`} className="text-[11px] text-slate-500">
                            {source.label}
                          </span>
                        ),
                      )}
                    </div>
                  )}
                </>
              ) : (
                /* 查不到就如实说"没有"，而不是假装这条不存在、也不是编一段 */
                <p className="text-xs text-slate-500 leading-relaxed mt-2">
                  {entry.note || '知识库里没有该地点的条目，未提供背景说明（不编造）。'}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function AccessibilityPanel({ proposals }: { proposals?: any[] }) {
  const proposal = (proposals || []).find((item: any) => item?.agent_id === 'accessibility-agent');
  const entries: AccessEntry[] = Array.isArray(proposal?.candidates) ? proposal.candidates : [];
  if (!proposal || entries.length === 0) return null;

  const verifiedCount = entries.filter((item) => item.verified).length;
  const checklists = entries.find((item) => (item.checklist || []).length > 0)?.checklist || [];

  return (
    <section
      data-testid="accessibility-panel"
      data-verified-count={verifiedCount}
      data-unverified-count={entries.length - verifiedCount}
      className="mb-6 rounded-2xl border border-slate-200 bg-slate-50/60 p-4"
    >
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <Accessibility className="w-4 h-4 text-slate-600" />
        <h4 className="text-sm font-black text-slate-800">无障碍信息</h4>
        {/* 没有任何官方资料时**不许**说"完全无障碍" —— 直接说未核实 */}
        <span
          data-testid="accessibility-summary"
          className="text-[11px] font-bold text-rose-700 bg-rose-50 border border-rose-200 rounded px-2 py-0.5"
        >
          {verifiedCount === 0
            ? '无障碍信息：未核实'
            : `已核实 ${verifiedCount} / 共 ${entries.length}`}
        </span>
      </div>

      <p className="text-xs text-slate-600 leading-relaxed">
        {verifiedCount === 0
          ? '我们没有这些地点的官方无障碍资料，因此**不判断**是否方便通行。请向景区确认以下事项。'
          : '已核实的项目如下；未列出的项目仍未核实，建议出行前确认。'}
      </p>

      {checklists.length > 0 && (
        <div className="mt-2">
          <span className="text-[11px] text-slate-500">建议确认：</span>
          <ul data-testid="accessibility-checklist" className="mt-1 space-y-0.5">
            {checklists.map((item, index) => (
              <li key={`check-${index}`} className="text-xs text-slate-700">
                · {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3 space-y-1.5">
        {entries.map((entry, index) => (
          <div
            key={`access-${index}`}
            data-testid="accessibility-entry"
            data-verified={entry.verified ? 'true' : 'false'}
            className="flex items-center gap-2 flex-wrap bg-white/80 rounded-lg border border-slate-100 px-3 py-1.5"
          >
            <span className="text-xs font-bold text-slate-700">{entry.name}</span>
            <span
              className={`text-[11px] rounded px-2 py-0.5 border ${
                entry.verified ? TRUST_STYLES.verified : 'bg-rose-50 text-rose-700 border-rose-200'
              }`}
            >
              {entry.status_text || (entry.verified ? '无障碍信息：已核实' : '无障碍信息：未核实')}
            </span>
            {/* 已核实项把具体内容摆出来；未核实项不显示"猜测值" */}
            {entry.verified &&
              Object.entries(entry.facilities || {})
                .filter(([, value]) => value && value !== 'unknown')
                .map(([key, value]) => (
                  <span key={`fac-${index}-${key}`} className="text-[11px] text-slate-600">
                    {key}：{value}
                  </span>
                ))}
          </div>
        ))}
      </div>
    </section>
  );
}

export default function SpecialistInsightsPanel({ proposals }: { proposals?: any[] }) {
  return (
    <>
      <CulturePanel proposals={proposals} />
      <AccessibilityPanel proposals={proposals} />
    </>
  );
}
