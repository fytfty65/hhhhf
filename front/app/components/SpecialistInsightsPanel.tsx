'use client';

/**
 * 行程里的「当地文化」与「无障碍」两块内容（P1 的渲染端）。
 *
 * 设计取向：**读起来像人写的行程小贴士，不像机器吐出来的字段表**。
 * 具体做法（都是有意的，不是随手写的）：
 *
 *  · 不摆字段表。文化条目写成连贯的散文 + 一句礼仪提醒，
 *    不要「background: ... / etiquette: ...」这种键值罗列。
 *  · 不显示内部术语。`knowledge_base`、`source_type` 这类枚举值不上屏；
 *    出处收进底部一行小字，而不是每个卡片挂一堆徽章。
 *  · 卡片走"纸质小册子"质感：细分隔线、克制的配色、少量强调色，
 *    不用大色块 + 圆角徽章堆叠（那种最像 AI 生成的界面）。
 *  · 一次只展开一个地点，默认展开第一条 —— 信息密度降下来，扫一眼就能读完。
 *
 * 诚实口径（与后端一致，必须原样传达给用户）：
 *  · 知识库里查不到的地点**照样列出来**，写清"没有可靠来源，未提供背景说明"。
 *    悄悄不显示会让人以为漏了；如实显示才知道是刻意留白。
 *  · 无障碍没有官方资料时**只写"未核实"**，并列出该向景区确认的事项。
 *    绝不出现"无障碍设施齐全"这类没有依据的断言。
 */

import { useState } from 'react';
import { ChevronDown, ExternalLink, ArrowUpRight } from 'lucide-react';

type CultureSource = { label?: string; url?: string };

type CultureEntry = {
  name: string;
  background?: string;
  etiquette?: string;
  recommended_stay?: string;
  sources?: CultureSource[];
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

/** 取出某个智能体的候选；没有就返回空数组（不渲染空壳） */
function candidatesOf(proposals: any[] | undefined, agentId: string): any[] {
  const proposal = (proposals || []).find((item: any) => item?.agent_id === agentId);
  return Array.isArray(proposal?.candidates) ? proposal.candidates : [];
}

/** 把 2026-10-08 写成「2026 年 10 月 8 日」—— 界面上不出现 ISO 字符串 */
function readableDate(value?: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
  if (!match) return '';
  return `${match[1]} 年 ${Number(match[2])} 月 ${Number(match[3])} 日`;
}

/** 无障碍四项：后端给的是内部键，这里换成用户能照着问景区的话 */
const FACILITY_LABELS: Record<string, string> = {
  入口坡度: '入口坡道与台阶',
  电梯: '电梯',
  无障碍卫生间: '无障碍卫生间',
  接驳车: '接驳车',
};

export function CulturePanel({ proposals }: { proposals?: any[] }) {
  const entries: CultureEntry[] = candidatesOf(proposals, 'culture-agent');
  const [openIndex, setOpenIndex] = useState(0);
  if (entries.length === 0) return null;

  const sourced = entries.filter((item) => (item.sources || []).length > 0);
  const missingCount = entries.length - sourced.length;

  return (
    <section
      data-testid="culture-panel"
      data-sourced-count={sourced.length}
      className="mb-8 overflow-hidden rounded-2xl border border-stone-200/80 bg-[#fdfcfa] shadow-[0_1px_2px_rgba(28,25,23,0.04)]"
    >
      {/* 报头：像一本小册子的扉页，而不是"AI 面板标题栏" */}
      <header className="flex items-baseline justify-between gap-3 border-b border-stone-200/70 px-5 py-4">
        <div className="min-w-0">
          <h3 className="text-[15px] font-bold tracking-wide text-stone-800">当地文化</h3>
          <p className="mt-1 text-[11px] leading-4 text-stone-500">背景与礼仪，出门前读一眼就够</p>
        </div>
        <span className="shrink-0 text-[11px] tabular-nums text-stone-400">
          收录 {sourced.length} 处
          {missingCount > 0 && ` · 另有 ${missingCount} 处暂无资料`}
        </span>
      </header>

      <ul className="divide-y divide-stone-200/60">
        {entries.map((entry, index) => {
          const hasBackground = Boolean(entry.background);
          const isOpen = openIndex === index;
          const panelId = `culture-detail-${index}`;
          return (
            <li
              key={`culture-${index}`}
              data-testid="culture-entry"
              data-has-background={hasBackground ? 'true' : 'false'}
            >
              <button
                type="button"
                aria-expanded={isOpen}
                aria-controls={panelId}
                onClick={() => setOpenIndex(isOpen ? -1 : index)}
                className="flex w-full cursor-pointer items-center gap-3 px-5 py-3 text-left transition-colors hover:bg-stone-50"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-semibold text-stone-800">
                    {entry.name}
                  </span>
                  {/* 收起时给一句摘要，省得每条都要点开才知道有没有内容 */}
                  {!isOpen && (
                    <span className="mt-0.5 block truncate text-[11px] text-stone-500">
                      {hasBackground
                        ? entry.recommended_stay
                          ? `建议停留 ${entry.recommended_stay}`
                          : '有背景介绍'
                        : '暂无可靠来源，未提供背景'}
                    </span>
                  )}
                </span>
                {entry.recommended_stay && isOpen && (
                  <span className="hidden shrink-0 text-[11px] text-stone-500 sm:block">
                    建议停留 {entry.recommended_stay}
                  </span>
                )}
                <ChevronDown
                  className={`h-4 w-4 shrink-0 text-stone-400 transition-transform duration-200 ${
                    isOpen ? 'rotate-180' : ''
                  }`}
                />
              </button>

              {isOpen && (
                <div id={panelId} className="px-5 pb-5 pt-0">
                  {hasBackground ? (
                    <div className="max-w-[60ch]">
                      <p className="text-[13px] leading-7 text-stone-700">{entry.background}</p>
                      {entry.etiquette && (
                        <p className="mt-3 border-l-2 border-stone-300 pl-3 text-[12px] leading-6 text-stone-600">
                          <span className="font-semibold text-stone-700">入乡随俗 </span>
                          {entry.etiquette}
                        </p>
                      )}
                      {/* 出处与更新时间收进底部一行小字：要核对时找得到，平时不抢视线 */}
                      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-stone-400">
                        {(entry.sources || []).length > 0 && (
                          <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
                            <span>来源</span>
                            {(entry.sources || []).map((source, sourceIndex) =>
                              source.url ? (
                                <a
                                  key={`src-${index}-${sourceIndex}`}
                                  href={source.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="inline-flex items-center gap-0.5 text-stone-500 underline decoration-stone-300 underline-offset-2 transition-colors hover:text-orange-600 hover:decoration-orange-300"
                                >
                                  {source.label || '查看'}
                                  <ExternalLink className="h-3 w-3" />
                                </a>
                              ) : (
                                <span key={`src-${index}-${sourceIndex}`}>{source.label}</span>
                              ),
                            )}
                          </span>
                        )}
                        {entry.reviewed_at && <span>{readableDate(entry.reviewed_at)} 复核</span>}
                      </div>
                    </div>
                  ) : (
                    /* 查不到就如实说"没有"，不假装这条不存在、也不编一段 */
                    <p className="max-w-[60ch] text-[12px] leading-6 text-stone-500">
                      {entry.note || '暂无可靠来源，未提供背景说明。'}
                    </p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <footer className="border-t border-stone-200/70 bg-stone-50/60 px-5 py-2.5">
        <p className="text-[11px] leading-4 text-stone-400">
          以上背景整理自公开资料，供行程参考；开放时间、票价等以景区现场公告为准。
        </p>
      </footer>
    </section>
  );
}

export function AccessibilityPanel({ proposals }: { proposals?: any[] }) {
  const entries: AccessEntry[] = candidatesOf(proposals, 'accessibility-agent');
  if (entries.length === 0) return null;

  const verified = entries.filter((item) => item.verified);
  const unverifiedCount = entries.length - verified.length;
  const checklist = entries.find((item) => (item.checklist || []).length > 0)?.checklist || [];
  const allUnverified = verified.length === 0;

  return (
    <section
      data-testid="accessibility-panel"
      data-verified-count={verified.length}
      data-unverified-count={unverifiedCount}
      className="mb-8 overflow-hidden rounded-2xl border border-stone-200/80 bg-white shadow-[0_1px_2px_rgba(28,25,23,0.04)]"
    >
      <header className="flex items-baseline justify-between gap-3 border-b border-stone-200/70 px-5 py-4">
        <div className="min-w-0">
          <h3 className="text-[15px] font-bold tracking-wide text-stone-800">无障碍通行</h3>
          <p className="mt-1 text-[11px] leading-4 text-stone-500">
            有需要时提前问清楚，比到了再说强
          </p>
        </div>
        <span
          data-testid="accessibility-summary"
          className="shrink-0 text-[11px] tabular-nums text-stone-400"
        >
          {allUnverified ? `${unverifiedCount} 处待确认` : `已确认 ${verified.length} 处`}
        </span>
      </header>

      <div className="px-5 py-4">
        {/* 没有官方资料时**不判断**是否方便通行 —— 只说未核实 */}
        <p className="max-w-[60ch] text-[12px] leading-6 text-stone-600">
          {allUnverified
            ? '我们手头没有这些地点的官方无障碍资料，因此不对能否顺利通行下结论。下面的情况建议出发前致电景区确认。'
            : '已确认的信息列在下方；其余地点仍未核实，建议出发前再确认一次。'}
        </p>

        {checklist.length > 0 && (
          <ul data-testid="accessibility-checklist" className="mt-4 grid gap-1.5 sm:grid-cols-2">
            {checklist.map((item, index) => (
              <li
                key={`check-${index}`}
                className="flex items-start gap-2 text-[12px] leading-6 text-stone-700"
              >
                <span className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-stone-400" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ul className="divide-y divide-stone-200/60 border-t border-stone-200/70">
        {entries.map((entry, index) => {
          const known = Object.entries(entry.facilities || {}).filter(
            ([, value]) => value && value !== 'unknown',
          );
          return (
            <li
              key={`access-${index}`}
              data-testid="accessibility-entry"
              data-verified={entry.verified ? 'true' : 'false'}
              className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 py-2.5"
            >
              <span className="text-[12px] font-semibold text-stone-800">{entry.name}</span>
              {entry.verified ? (
                known.length > 0 ? (
                  <span className="text-[11px] leading-5 text-stone-500">
                    {known
                      .map(([key, value]) => `${FACILITY_LABELS[key] || key}${value}`)
                      .join(' · ')}
                  </span>
                ) : (
                  <span className="text-[11px] text-stone-500">已确认</span>
                )
              ) : (
                /* 未核实不写"暂无/未知"这类含糊词，直接说要不要去问 */
                <span className="text-[11px] text-stone-400">未核实 · 建议致电确认</span>
              )}
            </li>
          );
        })}
      </ul>

      <footer className="border-t border-stone-200/70 bg-stone-50/60 px-5 py-2.5">
        <p className="text-[11px] leading-4 text-stone-400">
          无障碍设施以景区实际开放情况为准，出行前建议再确认一次。
        </p>
      </footer>
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

/** 折叠入口：把"还有这些参考信息"收起来，不占正文位置 */
export function SpecialistInsightsDisclosure({ proposals }: { proposals?: any[] }) {
  const [open, setOpen] = useState(false);
  const cultureCount = candidatesOf(proposals, 'culture-agent').length;
  const accessCount = candidatesOf(proposals, 'accessibility-agent').length;
  if (cultureCount === 0 && accessCount === 0) return null;

  return (
    <div className="mb-8">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex cursor-pointer items-center gap-1.5 text-[12px] text-stone-500 transition-colors hover:text-orange-600"
      >
        行程参考：当地文化{cultureCount > 0 ? `（${cultureCount} 处）` : ''}
        {accessCount > 0 ? '、无障碍通行' : ''}
        <ArrowUpRight className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="mt-4">
          <CulturePanel proposals={proposals} />
          <AccessibilityPanel proposals={proposals} />
        </div>
      )}
    </div>
  );
}
